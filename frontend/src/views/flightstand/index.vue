<template>
  <section class="page" data-module="flightstand">
    <header class="page-head">
      <div>
        <h2>机位分配状态看板</h2>
        <p class="page-desc">
          每个机位一张卡片：分配后卡片底色与引导线一起翻新，释放即回到空闲并清空占用；
          列表、详情与看板共用服务端同一份分配明细，已占机位不可重复分配。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportBoard">导出机位分配清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article
        v-for="item in statCards"
        :key="item.label"
        class="stat-card"
        :class="{ 'stat-card--active': statusFilter === item.status }"
        @click="toggleStatus(item.status)"
      >
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>机位编号 / 航段 / 航班</span>
        <input v-model="keyword" placeholder="如 201、CA1858" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <span v-if="notice" class="board-notice" :class="notice.tone">{{ notice.text }}</span>
    </form>

    <div v-if="stands.length" class="stand-grid">
      <article
        v-for="stand in stands"
        :key="stand.id"
        class="stand-card"
        :class="`stand-card--${statusKey(stand.status)}`"
      >
        <header class="stand-card-head">
          <div>
            <strong class="stand-code">{{ stand['机位编号'] }}</strong>
            <span class="stand-type">{{ stand['机位类型'] }} · {{ stand['可停机型'] }}</span>
          </div>
          <span class="stand-badge" :class="`stand-badge--${statusKey(stand.status)}`">{{ stand.status }}</span>
        </header>

        <!-- 引导线：与卡片底色同步翻新 -->
        <div class="guide-line" :class="`guide-line--${guideKey(stand['引导线状态'])}`">
          <span class="guide-dot" />
          引导线{{ stand['引导线状态'] }}
          <span class="guide-bridge">廊桥：{{ stand['廊桥配置'] || '—' }}</span>
        </div>

        <dl class="stand-meta">
          <div><dt>占用状态</dt><dd>{{ stand['占用状态'] }}</dd></div>
          <div><dt>对应航班</dt><dd>{{ stand['对应航班'] || '—' }}</dd></div>
          <div class="stand-meta-wide"><dt>分配航段</dt><dd>{{ stand['分配航段'] || '—' }}</dd></div>
        </dl>

        <div v-if="cardNotices[stand.id]" class="card-notice" :class="cardNotices[stand.id].tone">
          <span>{{ cardNotices[stand.id].text }}</span>
          <button type="button" class="notice-close" @click="clearNotice(stand.id)">×</button>
        </div>

        <!-- 空闲机位：录入航段后提交分配 -->
        <form v-if="stand.status === '空闲'" class="stand-actions" @submit.prevent="allocate(stand)">
          <input
            v-model="segmentInputs[stand.id]"
            placeholder="分配航段，如 CA1234/北京首都-上海虹桥"
            :disabled="busyId === stand.id"
          />
          <button class="btn primary" type="submit" :disabled="busyId === stand.id">
            {{ busyId === stand.id ? '落库中…' : '分配机位' }}
          </button>
          <button class="btn" type="button" @click="openDetail(stand.id)">详情</button>
        </form>

        <div v-else-if="stand.status === '已分配'" class="stand-actions">
          <button
            class="btn danger"
            type="button"
            :disabled="busyId === stand.id"
            @click="release(stand)"
          >
            {{ busyId === stand.id ? '处理中…' : '释放机位' }}
          </button>
          <button class="btn" type="button" @click="openDetail(stand.id)">详情</button>
        </div>

        <div v-else class="stand-actions">
          <button
            class="btn"
            type="button"
            :disabled="busyId === stand.id"
            @click="reopen(stand)"
          >
            恢复开放
          </button>
          <button class="btn" type="button" @click="openDetail(stand.id)">详情</button>
        </div>
      </article>
    </div>
    <div v-else-if="loaded" class="board-empty">没有符合条件的机位，试试重置筛选条件。</div>

    <!-- 分配明细：占用张数跟着它重算 -->
    <section class="ledger">
      <h3>分配明细（生效中 {{ allocations.length }} 条，看板占用张数据此重算）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>#</th><th>机位编号</th><th>对应航班</th><th>分配航段</th>
            <th>操作员</th><th>分配时间</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alloc in allocations" :key="alloc.id">
            <td>{{ alloc.id }}</td>
            <td>{{ alloc['机位编号'] }}</td>
            <td>{{ alloc['对应航班'] }}</td>
            <td>{{ alloc['分配航段'] }}</td>
            <td>{{ alloc['操作员'] }}</td>
            <td>{{ alloc['分配时间'] }}</td>
            <td><span class="ledger-tag">{{ alloc['状态'] }}</span></td>
          </tr>
          <tr v-if="!allocations.length">
            <td colspan="7" class="empty-state">当前没有生效中的分配，占用张数为 0</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 详情抽屉：与卡片、列表同源 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>机位 {{ detail['机位编号'] }} 详情</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>
        <dl class="drawer-meta">
          <div><dt>机位状态</dt><dd>{{ detail.status }}</dd></div>
          <div><dt>占用状态</dt><dd>{{ detail['占用状态'] }}</dd></div>
          <div><dt>引导线状态</dt><dd>{{ detail['引导线状态'] }}</dd></div>
          <div><dt>机位类型</dt><dd>{{ detail['机位类型'] }}</dd></div>
          <div><dt>可停机型</dt><dd>{{ detail['可停机型'] }}</dd></div>
          <div><dt>廊桥配置</dt><dd>{{ detail['廊桥配置'] || '—' }}</dd></div>
          <div class="drawer-meta-wide"><dt>分配航段</dt><dd>{{ detail['分配航段'] || '—' }}</dd></div>
        </dl>

        <h4>该机位分配历史</h4>
        <table class="data-table">
          <thead>
            <tr><th>航段</th><th>航班</th><th>分配时间</th><th>释放时间</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in detail['分配明细']" :key="item.id">
              <td>{{ item['分配航段'] }}</td>
              <td>{{ item['对应航班'] }}</td>
              <td>{{ item['分配时间'] }}</td>
              <td>{{ item['释放时间'] || '—' }}</td>
              <td>{{ item['状态'] }}</td>
            </tr>
            <tr v-if="!detail['分配明细'].length">
              <td colspan="5" class="empty-state">该机位暂无分配记录</td>
            </tr>
          </tbody>
        </table>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type StandStatus = '空闲' | '已分配' | '检修中'

interface Allocation {
  id: number
  机位id: number
  机位编号: string
  分配航段: string
  对应航班: string
  操作员: string
  分配时间: string
  释放时间: string
  状态: '生效中' | '已释放'
}

interface Stand {
  id: number
  status: StandStatus
  机位编号: string
  机位类型: string
  可停机型: string
  廊桥配置: string
  引导线状态: string
  占用状态: string
  分配航段: string
  对应航班: string
}

interface StandDetail extends Stand {
  分配明细: Allocation[]
}

interface BoardStats {
  总机位数: number
  空闲: number
  已分配: number
  检修中: number
  占用张数: number
}

interface BoardPayload {
  stands: Stand[]
  allocations: Allocation[]
  stats: BoardStats
}

interface ActionResponse {
  ok: boolean
  message: string
  conflict: boolean
  duplicated: boolean
  entry: Stand | null
}

const ENDPOINT = '/api/flightstand'

const stands = ref<Stand[]>([])
const allocations = ref<Allocation[]>([])
const stats = ref<BoardStats>({ 总机位数: 0, 空闲: 0, 已分配: 0, 检修中: 0, 占用张数: 0 })
const loaded = ref(false)
const keyword = ref('')
const statusFilter = ref<StandStatus | ''>('')
const busyId = ref<number | null>(null)
const segmentInputs = reactive<Record<number, string>>({})
const cardNotices = reactive<Record<number, { text: string; tone: 'conflict' | 'duplicated' | 'info' }>>({})
const notice = ref<{ text: string; tone: 'conflict' | 'duplicated' | 'info' } | null>(null)
const detail = ref<StandDetail | null>(null)

const statCards = computed(() => [
  { label: '总机位数', value: stats.value.总机位数, status: '' as const },
  { label: '空闲机位', value: stats.value.空闲, status: '空闲' as const },
  { label: '占用张数（生效中分配）', value: stats.value.占用张数, status: '已分配' as const },
  { label: '检修中机位', value: stats.value.检修中, status: '检修中' as const },
])

function statusKey(status: string): string {
  if (status === '已分配') return 'busy'
  if (status === '检修中') return 'maintenance'
  return 'free'
}

function guideKey(guide: string): string {
  if (guide === '占用') return 'busy'
  if (guide === '封锁') return 'locked'
  return 'free'
}

function toggleStatus(status: '' | StandStatus) {
  statusFilter.value = statusFilter.value === status ? '' : status
  void reload()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function clearNotice(id: number) {
  delete cardNotices[id]
}

function setCardNotice(id: number, text: string, tone: 'conflict' | 'duplicated' | 'info') {
  cardNotices[id] = { text, tone }
}

async function reload() {
  notice.value = null
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  const query = params.toString()
  try {
    const response = await request(`${ENDPOINT}/board${query ? `?${query}` : ''}`)
    if (!response.ok) throw new Error('机位看板读取失败')
    const payload = (await response.json()) as BoardPayload
    // 服务端落库成功后的这份结果同时驱动卡片与占用张数。
    stands.value = payload.stands
    allocations.value = payload.allocations
    stats.value = payload.stats
    loaded.value = true
  } catch (error) {
    notice.value = {
      text: error instanceof Error ? error.message : '机位看板读取失败',
      tone: 'conflict',
    }
  }
}

async function postAction(
  stand: Stand,
  values: Record<string, string>,
): Promise<ActionResponse | null> {
  busyId.value = stand.id
  try {
    const response = await request(`${ENDPOINT}/${stand.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    return (await response.json()) as ActionResponse
  } catch (error) {
    setCardNotice(
      stand.id,
      error instanceof Error ? error.message : '请求未送达，请重试',
      'conflict',
    )
    return null
  } finally {
    busyId.value = null
  }
}

async function allocate(stand: Stand) {
  const segment = (segmentInputs[stand.id] ?? '').trim()
  if (!segment) {
    setCardNotice(stand.id, '请先填写分配航段', 'info')
    return
  }
  const result = await postAction(stand, { action: '分配机位', 分配航段: segment })
  if (!result) return
  // 无论成功、幂等还是冲突，都以服务端返回为准重新拉取，保证三处口径一致。
  await reload()
  if (result.ok && result.duplicated) {
    setCardNotice(stand.id, result.message, 'duplicated')
  } else if (!result.ok && result.conflict) {
    // 冲突：卡片保持原占用，原因写明在卡片上。
    setCardNotice(stand.id, result.message, 'conflict')
  } else if (result.ok) {
    segmentInputs[stand.id] = ''
    notice.value = { text: result.message, tone: 'info' }
  } else {
    setCardNotice(stand.id, result.message, 'info')
  }
}

async function release(stand: Stand) {
  const result = await postAction(stand, { action: '释放机位' })
  if (!result) return
  await reload()
  if (result.ok) {
    notice.value = { text: result.message, tone: 'info' }
  } else {
    setCardNotice(stand.id, result.message, result.conflict ? 'conflict' : 'info')
  }
}

async function reopen(stand: Stand) {
  const result = await postAction(stand, { action: '恢复开放' })
  if (!result) return
  await reload()
  notice.value = { text: result.message, tone: result.ok ? 'info' : 'conflict' }
}

async function openDetail(id: number) {
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) throw new Error('机位详情读取失败')
    detail.value = (await response.json()) as StandDetail
  } catch (error) {
    notice.value = {
      text: error instanceof Error ? error.message : '机位详情读取失败',
      tone: 'conflict',
    }
  }
}

function closeDetail() {
  detail.value = null
}

function exportBoard() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(reload)
</script>

<style scoped>
.board-notice {
  margin-left: auto;
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 6px;
}

.stat-card {
  cursor: pointer;
  user-select: none;
}
.stat-card--active {
  outline: 2px solid var(--brand);
}

.stand-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.stand-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  border-left-width: 5px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.stand-card--free { border-left-color: #16a34a; }
.stand-card--busy { border-left-color: var(--brand); background: #eef5ff; }
.stand-card--maintenance { border-left-color: #d97706; background: #fff7ed; }

.stand-card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.stand-code { font-size: 18px; }
.stand-type { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }

.stand-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  color: #fff;
}
.stand-badge--free { background: #16a34a; }
.stand-badge--busy { background: var(--brand); }
.stand-badge--maintenance { background: #d97706; }

/* 引导线占用随卡片状态一起翻新 */
.guide-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 6px;
}
.guide-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}
.guide-line--free { color: #15803d; background: #dcfce7; }
.guide-line--busy { color: #1d4ed8; background: #dbeafe; }
.guide-line--locked { color: #b45309; background: #fef3c7; }
.guide-bridge { margin-left: auto; color: var(--muted); }

.stand-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  margin: 0;
  font-size: 12px;
}
.stand-meta dt { color: var(--muted); }
.stand-meta dd { margin: 0; }
.stand-meta-wide { grid-column: 1 / -1; }

.stand-actions {
  display: flex;
  gap: 6px;
  margin-top: auto;
}
.stand-actions input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 12px;
}
.btn.danger {
  border-color: #fecaca;
  color: #b42318;
  background: #fff1f2;
}

.card-notice {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 6px;
  line-height: 1.5;
}
.card-notice.conflict { background: #fef2f2; color: #b42318; border: 1px solid #fecaca; }
.card-notice.duplicated { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.card-notice.info { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.notice-close {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  color: inherit;
}

.board-empty {
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 24px;
  text-align: center;
  color: var(--muted);
  margin-bottom: 20px;
}

.ledger h3 { font-size: 14px; margin: 4px 0 8px; }
.ledger-tag {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
}

.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.drawer {
  width: 560px;
  max-width: 92vw;
  background: #fff;
  height: 100%;
  overflow-y: auto;
  padding: 16px 18px;
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.15);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.drawer-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
  margin: 12px 0;
  font-size: 13px;
}
.drawer-meta dt { color: var(--muted); font-size: 12px; }
.drawer-meta dd { margin: 2px 0 0; }
.drawer-meta-wide { grid-column: 1 / -1; }
.drawer h4 { font-size: 13px; margin: 16px 0 8px; }
</style>
