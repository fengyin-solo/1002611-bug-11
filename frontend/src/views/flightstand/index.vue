<template>
  <section class="page" data-module="flightstand">
    <header class="page-head">
      <div>
        <h2>机位分配状态看板</h2>
        <p class="page-desc">每个机位一张卡片，状态以服务端落库的分配明细为准：分配后卡片底色与引导线一起翻新，释放即回到空闲并清空占用。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记机位</button>
        <button class="btn" type="button" @click="exportRows">导出机位分配清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card" :class="item.tone">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>机位编号</span>
        <input v-model="filters.keyword" placeholder="按机位编号检索" />
      </label>
      <label class="filter-item">
        <span>机位状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="stand-grid">
      <article
        v-for="card in items"
        :key="String(card.id)"
        class="stand-card"
        :class="[`tone-${card.status}`, { flashing: cardMessages[card.id]?.conflict }]"
      >
        <header class="stand-card-head">
          <strong class="stand-no">{{ card['机位编号'] }}</strong>
          <span class="stand-status" :class="`dot-${card.status}`">{{ card.status }}</span>
        </header>

        <dl class="stand-meta">
          <div><dt>机位类型</dt><dd>{{ card['机位类型'] ?? '—' }}</dd></div>
          <div><dt>可停机型</dt><dd>{{ card['可停机型'] ?? '—' }}</dd></div>
          <div><dt>廊桥配置</dt><dd>{{ card['廊桥配置'] ?? '—' }}</dd></div>
          <div>
            <dt>引导线</dt>
            <dd :class="card['引导线状态'] === '占用' ? 'line-busy' : 'line-free'">
              {{ card['引导线状态'] === '占用' ? '🟢 占用（引导中）' : '⚪ 空闲' }}
            </dd>
          </div>
          <div><dt>占用状态</dt><dd>{{ card['占用状态'] }}</dd></div>
          <div><dt>分配航段</dt><dd class="stand-leg">{{ card['分配航段'] ?? '—' }}</dd></div>
        </dl>

        <!-- 冲突原因只在卡片上临时标注，以刷新后的服务端结果为准 -->
        <p v-if="cardMessages[card.id]" class="card-message" :class="cardMessages[card.id].conflict ? 'is-conflict' : 'is-info'">
          {{ cardMessages[card.id].text }}
        </p>

        <footer class="stand-actions">
          <button
            v-if="card.status === '空闲'"
            class="btn primary small"
            type="button"
            @click="openAllocate(card)"
          >
            分配机位
          </button>
          <button
            v-if="card.status === '检修中'"
            class="btn primary small"
            type="button"
            @click="runAction(card, '解除检修')"
          >
            解除检修
          </button>
          <button
            v-if="card['占用状态'] === '占用'"
            class="btn small"
            type="button"
            @click="runAction(card, '释放机位')"
          >
            释放机位
          </button>
          <button
            v-if="card.status === '空闲'"
            class="btn small"
            type="button"
            @click="runAction(card, '登记检修')"
          >
            登记检修
          </button>
          <button class="link small" type="button" @click="openDetail(card.id)">查看详情</button>
        </footer>
      </article>
      <p v-if="!items.length" class="empty-state board-empty">当前筛选条件下没有机位</p>
    </div>

    <footer class="page-foot">
      <span>共 {{ stats['机位总数'] ?? 0 }} 个机位，占用 {{ stats['占用机位'] ?? 0 }} 个，生效分配明细 {{ stats['生效分配明细'] ?? 0 }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 分配弹窗 -->
    <div v-if="allocateTarget" class="modal-mask" @click.self="closeAllocate">
      <form class="modal-panel" @submit.prevent="submitAllocate">
        <h3>分配机位 {{ allocateTarget['机位编号'] }}</h3>
        <p class="modal-hint">同一航段重复提交只生效第一次；机位已被占用时会直接拦下并在卡片上注明冲突原因。</p>
        <label class="form-row">
          <span>分配航段 <em>*</em></span>
          <input v-model="allocateForm.航段" placeholder="例：CA1832 上海虹桥-北京首都" />
        </label>
        <label class="form-row">
          <span>航班号</span>
          <input v-model="allocateForm.航班号" placeholder="例：CA1832" />
        </label>
        <label class="form-row">
          <span>操作人</span>
          <input v-model="allocateForm.操作人" :placeholder="session.operator" />
        </label>
        <p v-if="allocateError" class="error-text">{{ allocateError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeAllocate">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">{{ submitting ? '提交中…' : '确认分配' }}</button>
        </div>
      </form>
    </div>

    <!-- 详情弹窗：数据走 GET /{id}，与看板、列表同源 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal-panel wide">
        <h3>机位详情 · {{ detail['机位编号'] }}</h3>
        <dl class="stand-meta detail-grid">
          <div><dt>机位状态</dt><dd>{{ detail['机位状态'] }}</dd></div>
          <div><dt>机位类型</dt><dd>{{ detail['机位类型'] ?? '—' }}</dd></div>
          <div><dt>可停机型</dt><dd>{{ detail['可停机型'] ?? '—' }}</dd></div>
          <div><dt>廊桥配置</dt><dd>{{ detail['廊桥配置'] ?? '—' }}</dd></div>
          <div><dt>引导线状态</dt><dd>{{ detail['引导线状态'] }}</dd></div>
          <div><dt>占用状态</dt><dd>{{ detail['占用状态'] }}</dd></div>
          <div><dt>分配航段</dt><dd>{{ detail['分配航段'] ?? '—' }}</dd></div>
        </dl>
        <h4 class="detail-title">分配明细</h4>
        <table class="data-table detail-table">
          <thead>
            <tr>
              <th>序号</th><th>航段</th><th>航班号</th><th>操作人</th><th>分配时刻</th><th>状态</th><th>释放时刻</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in detail.allocations" :key="row.序号">
              <td>{{ row.序号 }}</td>
              <td>{{ row.航段 }}</td>
              <td>{{ row.航班号 ?? '—' }}</td>
              <td>{{ row.操作人 ?? '—' }}</td>
              <td>{{ row.分配时刻 }}</td>
              <td :class="row.状态 === '生效' ? 'line-busy' : ''">{{ row.状态 }}</td>
              <td>{{ row.释放时刻 ?? '—' }}</td>
            </tr>
            <tr v-if="!detail.allocations.length">
              <td colspan="7" class="empty-state">该机位还没有分配记录</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type DetailRow = {
  序号: number
  航段: string
  航班号: string | null
  操作人: string | null
  分配时刻: string
  状态: '生效' | '已释放'
  释放时刻: string | null
}

type Stand = {
  id: number
  status: string
  机位编号: string
  机位状态: string
  机位类型: string | null
  可停机型: string | null
  廊桥配置: string | null
  引导线状态: string
  占用状态: string
  分配航段: string | null
  航班号: string | null
  操作人: string | null
  分配时刻: string | null
  allocations: DetailRow[]
}

type BoardStats = Record<string, number>

type BoardPayload = {
  stats: BoardStats
  items: Stand[]
}

type ActionPayload = {
  ok: boolean
  message: string
  conflict?: boolean
  entry?: Stand
}

const ENDPOINT = '/api/flightstand'
const statuses = ['空闲', '已分配', '占用中', '检修中']
const session = useSessionStore()

const items = ref<Stand[]>([])
const stats = ref<BoardStats>({})
const errorMessage = ref('')
const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })
const cardMessages = reactive<Record<number, { text: string; conflict: boolean }>>({})

const allocateTarget = ref<Stand | null>(null)
const allocateForm = reactive({ 航段: '', 航班号: '', 操作人: '' })
const allocateError = ref('')
const submitting = ref(false)

const detail = ref<Stand | null>(null)

const statCards = computed(() => [
  { label: '机位总数', value: stats.value['机位总数'] ?? 0, tone: '' },
  { label: '空闲机位', value: stats.value['空闲机位'] ?? 0, tone: 'tone-空闲' },
  { label: '占用机位', value: stats.value['占用机位'] ?? 0, tone: 'tone-已分配' },
  { label: '占用中（已到位）', value: stats.value['占用中机位'] ?? 0, tone: 'tone-占用中' },
  { label: '检修中机位', value: stats.value['检修中机位'] ?? 0, tone: 'tone-检修中' },
])

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '机位登记入口尚未接入审批流'
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword.trim()) query.set('keyword', filters.keyword.trim())
  if (filters.status) query.set('status', filters.status)
  try {
    const response = await request(`${ENDPOINT}/board?${query.toString()}`)
    if (!response.ok) {
      throw new Error('机位看板读取失败')
    }
    const payload = (await response.json()) as BoardPayload
    // 一切以服务端落库结果为准，成功读取后覆盖整板
    items.value = payload.items ?? []
    stats.value = payload.stats ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '机位看板读取失败'
  }
}

function openAllocate(card: Stand) {
  allocateTarget.value = card
  allocateForm.航段 = ''
  allocateForm.航班号 = ''
  allocateForm.操作人 = ''
  allocateError.value = ''
}

function closeAllocate() {
  allocateTarget.value = null
  allocateError.value = ''
}

async function submitAllocate() {
  if (!allocateTarget.value) return
  if (!allocateForm.航段.trim()) {
    allocateError.value = '请填写分配航段'
    return
  }
  await postAction(
    allocateTarget.value,
    '分配机位',
    { 航段: allocateForm.航段.trim(), 航班号: allocateForm.航班号.trim(), 操作人: allocateForm.操作人.trim() || session.operator },
  )
  if (!cardMessages[allocateTarget.value.id]?.conflict) {
    closeAllocate()
  }
}

async function runAction(card: Stand, action: string) {
  await postAction(card, action, {})
}

async function postAction(card: Stand, action: string, values: Record<string, string>) {
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/${card.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...values } }),
    })
    const payload = (await response.json()) as ActionPayload
    if (!response.ok || !payload.ok) {
      // 冲突：卡片保留原占用不动，把原因写到卡片上
      cardMessages[card.id] = { text: payload.message || '操作未生效', conflict: true }
      return
    }
    cardMessages[card.id] = { text: payload.message, conflict: false }
  } catch (error) {
    cardMessages[card.id] = {
      text: error instanceof Error ? error.message : '机位操作请求未送达，请稍后重试',
      conflict: true,
    }
  } finally {
    submitting.value = false
    // 服务端落库成功之后，再拉取看板，列表/详情/看板读到的是同一份结果
    await reload()
  }
}

async function openDetail(id: number) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) {
      throw new Error('机位详情读取失败')
    }
    detail.value = (await response.json()) as Stand
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '机位详情读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.stand-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.stand-card {
  background: #fff;
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stand-card.tone-空闲 { border-left-color: #16a34a; background: #f4fbf6; }
.stand-card.tone-已分配 { border-left-color: #1f6feb; background: #f2f7ff; }
.stand-card.tone-占用中 { border-left-color: #d97706; background: #fff8ef; }
.stand-card.tone-检修中 { border-left-color: #b42318; background: #fef3f2; }
.stand-card.flashing { animation: conflict-flash 0.9s ease 2; }

@keyframes conflict-flash {
  0%, 100% { box-shadow: 0 0 0 0 rgba(180, 35, 24, 0); }
  50% { box-shadow: 0 0 0 4px rgba(180, 35, 24, 0.25); }
}

.stat-card.tone-空闲 { border-top: 3px solid #16a34a; }
.stat-card.tone-已分配 { border-top: 3px solid #1f6feb; }
.stat-card.tone-占用中 { border-top: 3px solid #d97706; }
.stat-card.tone-检修中 { border-top: 3px solid #b42318; }

.stand-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.stand-no { font-size: 17px; }
.stand-status {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  color: #fff;
}
.dot-空闲 { background: #16a34a; }
.dot-已分配 { background: #1f6feb; }
.dot-占用中 { background: #d97706; }
.dot-检修中 { background: #b42318; }

.stand-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  margin: 0;
}
.stand-meta dt {
  font-size: 11px;
  color: var(--muted);
}
.stand-meta dd {
  margin: 0;
  font-size: 13px;
}
.stand-leg {
  grid-column: 1 / -1;
}
.line-busy { color: #1f6feb; font-weight: 600; }
.line-free { color: #16a34a; }

.card-message {
  margin: 0;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 6px;
}
.card-message.is-conflict { background: #fef3f2; color: #b42318; border: 1px solid #f3c6c0; }
.card-message.is-info { background: #eef4ff; color: #1f6feb; border: 1px solid #cdddf9; }

.stand-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: auto;
}
.btn.small, .link.small { font-size: 12px; padding: 4px 10px; }

.board-empty {
  grid-column: 1 / -1;
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 28px 0;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-panel {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 420px;
  max-width: calc(100vw - 32px);
}
.modal-panel.wide { width: 760px; }
.modal-panel h3 { margin: 0 0 8px; }
.modal-hint { font-size: 12px; color: var(--muted); margin: 0 0 12px; }
.form-row { display: block; margin-bottom: 10px; }
.form-row span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-row em { color: #b42318; font-style: normal; }
.form-row input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 13px;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }

.detail-grid { margin-bottom: 12px; }
.detail-title { font-size: 14px; margin: 4px 0 8px; }
.detail-table { font-size: 12px; }
</style>
