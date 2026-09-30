<script setup lang="ts">
/**
 * 5×7 点阵文字。
 *
 * 直接沿用既有海报的做法：手搓字模 + canvas 逐点绘制方点，硬边、像素感。
 * 在管理端用来画 404、空状态与计数，是整套界面里最有记忆点的元素。
 *
 * 只用得上几个字符，因此字模只包含数字与大写字母，不引入字体文件。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    text: string
    /** 次级行（小一号，走亮强调色） */
    sub?: string
    /** 主行颜色，默认取 --bone */
    color?: string
    /** 次级行颜色，默认取 --red-hi */
    subColor?: string
    /** 是否显示末尾闪烁光标 */
    cursor?: boolean
  }>(),
  { sub: '', color: '', subColor: '', cursor: false },
)

const FONT: Record<string, string[]> = {
  '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
  '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
  '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
  '3': ['11111', '00010', '00100', '00010', '00001', '10001', '01110'],
  '4': ['00100', '01100', '10100', '10100', '11111', '00100', '00100'],
  '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
  '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
  '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
  '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
  '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
  A: ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
  C: ['01110', '10001', '10000', '10000', '10000', '10001', '01110'],
  D: ['11110', '10001', '10001', '10001', '10001', '10001', '11110'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
  F: ['11111', '10000', '10000', '11110', '10000', '10000', '10000'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
  L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
  N: ['10001', '11001', '11001', '10101', '10011', '10011', '10001'],
  O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  P: ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
  S: ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
  T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  U: ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
  V: ['10001', '10001', '10001', '10001', '10001', '01010', '00100'],
  Y: ['10001', '10001', '01010', '00100', '00100', '00100', '00100'],
  ' ': ['00000', '00000', '00000', '00000', '00000', '00000', '00000'],
}

const CW = 5
const CH = 7
const TRACK = 1
const ROW_GAP = 2
const SMALL_K = 0.42

const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0
let blinkOn = true
let timer: ReturnType<typeof setInterval> | null = null

const colsOf = (text: string) => text.length * (CW + TRACK) - TRACK

const totalCols = computed(() =>
  Math.max(colsOf(props.text.toUpperCase()), (colsOf(props.sub.toUpperCase()) + TRACK + 1) * SMALL_K),
)
const totalRows = computed(() => CH + (props.sub ? ROW_GAP + CH * SMALL_K : 0))

const BLANK = ['00000', '00000', '00000', '00000', '00000', '00000', '00000']

function glyphFor(char: string): string[] {
  return FONT[char] ?? BLANK
}

function glyphCells(text: string, x0: number, y0: number, p: number, out: number[]): void {
  for (let i = 0; i < text.length; i += 1) {
    const glyph = glyphFor(text.charAt(i))
    const gx = x0 + i * (CW + TRACK) * p
    for (let row = 0; row < CH; row += 1) {
      const line = glyph[row] ?? ''
      for (let col = 0; col < CW; col += 1) {
        if (line.charAt(col) === '1') out.push(gx + (col + 0.5) * p, y0 + (row + 0.5) * p)
      }
    }
  }
}

function fillCells(
  ctx: CanvasRenderingContext2D,
  list: number[],
  size: number,
  color: string,
): void {
  if (list.length === 0) return
  const half = size / 2
  ctx.fillStyle = color
  for (let i = 0; i < list.length; i += 2) {
    const x = list[i] ?? 0
    const y = list[i + 1] ?? 0
    // 方点略大于格距，相邻格自然咬合，笔画连成一片
    ctx.fillRect(x - half, y - half, size, size)
  }
}

function resolveColor(variable: string, fallback: string): string {
  if (variable) return variable
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable || '')
  return value.trim() || fallback
}

function draw(): void {
  const element = canvas.value
  if (!element) return
  const width = element.getBoundingClientRect().width
  if (!width) return

  const ctx = element.getContext('2d')
  if (!ctx) return

  const p = width / totalCols.value
  const ps = p * SMALL_K
  const height = Math.ceil(totalRows.value * p)
  const dpr = Math.min(window.devicePixelRatio || 1, 3)

  if (element.width !== Math.round(width * dpr) || element.height !== Math.round(height * dpr)) {
    element.width = Math.round(width * dpr)
    element.height = Math.round(height * dpr)
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)

  const styles = getComputedStyle(document.documentElement)
  const mainColor = props.color || styles.getPropertyValue('--bone').trim() || '#cfcac4'
  const subColorValue = props.subColor || styles.getPropertyValue('--red-hi').trim() || '#ff4a55'
  const cursorColor = styles.getPropertyValue('--red').trim() || '#d0202f'

  const big: number[] = []
  glyphCells(props.text.toUpperCase(), 0, 0, p, big)
  fillCells(ctx, big, p * 1.06, mainColor)

  if (props.sub) {
    const small: number[] = []
    glyphCells(props.sub.toUpperCase(), 0, (CH + ROW_GAP) * p, ps, small)
    fillCells(ctx, small, ps * 1.06, subColorValue)

    if (props.cursor && blinkOn) {
      ctx.fillStyle = cursorColor
      ctx.fillRect(
        (colsOf(props.sub.toUpperCase()) + TRACK) * ps,
        (CH + ROW_GAP) * p,
        ps * 0.9,
        CH * ps,
      )
    }
  }
}

function schedule(): void {
  if (raf) return
  raf = requestAnimationFrame(() => {
    raf = 0
    draw()
  })
}

onMounted(() => {
  draw()
  window.addEventListener('resize', schedule)
  if (props.cursor) {
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (!calm) {
      timer = setInterval(() => {
        blinkOn = !blinkOn
        draw()
      }, 560)
    }
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', schedule)
  if (timer) clearInterval(timer)
  if (raf) cancelAnimationFrame(raf)
})

watch(() => [props.text, props.sub], schedule)
</script>

<template>
  <canvas
    ref="canvas"
    class="dots"
    role="img"
    :aria-label="sub ? `${text} ${sub}` : text"
    :style="{ aspectRatio: `${totalCols} / ${totalRows}` }"
  />
</template>

<style scoped>
.dots {
  display: block;
  width: 100%;
  height: auto;
}
</style>
