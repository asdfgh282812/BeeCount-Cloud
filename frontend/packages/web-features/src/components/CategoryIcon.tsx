import { CSSProperties } from 'react'

import { resolveMaterialIconName } from '../lib/categoryIconMap'
import { resolveCuteIconSvg } from '../lib/cuteCategoryIconMap'
import { useCategoryIconStyle } from '../context/CategoryIconStyleContext'

interface Props {
  icon: string | null | undefined
  iconType: string | null | undefined
  iconCloudFileId?: string | null
  iconPreviewUrlByFileId?: Record<string, string>
  className?: string
  style?: CSSProperties
  /** 字体像素大小,默认 20px(分类卡片小方块)。决定 Material Symbols 字号。 */
  size?: number
  /** 图标颜色,默认 `currentColor`(继承父元素)。 */
  color?: string
  /**
   * 可爱画风(cute)下,图示正下方的分类色底线颜色。不传就不画底线。
   * 只在全局 `useCategoryIconStyle()` 为 `'cute'` 时生效,material 画风忽略此项。
   * 呼叫端如果已经自己解析过分类颜色(例如二级分类继承父分类色),直接传解析
   * 好的颜色即可,不需要 `CategoryIcon` 重新查一次。
   */
  underlineColor?: string | null
}

/**
 * 分类图标渲染。三种数据来源按优先级:
 * 1. `iconType='custom'` + 云端文件 → 从 `iconPreviewUrlByFileId` 取预签 URL
 * 2. `iconType='custom'` + URL/data URI → 直接当 <img>
 * 3. 其他(material)→ Material Symbols Outlined 字体 ligature 渲染
 *
 * 字体通过 `index.html` 里的 Google Fonts CSS 子集化加载(见 categoryIconMap.ts)。
 * `font-display: swap` 保证字体未就绪时页面不 block,先渲文本占位、到货后瞬切。
 */
export function CategoryIcon({
  icon,
  iconType,
  iconCloudFileId,
  iconPreviewUrlByFileId,
  className,
  style,
  size = 20,
  color,
  underlineColor,
}: Props) {
  // Hooks 必须在任何 early return 之前无条件调用。
  const iconStyle = useCategoryIconStyle()
  const normalized = (icon || '').trim()
  const kind = (iconType || 'material').trim() || 'material'
  const cloudFileId = typeof iconCloudFileId === 'string' ? iconCloudFileId.trim() : ''
  const cloudPreview = cloudFileId ? iconPreviewUrlByFileId?.[cloudFileId] : undefined

  if (kind === 'custom' && cloudPreview) {
    return (
      <img
        alt=""
        className={className}
        src={cloudPreview}
        style={{ width: size, height: size, objectFit: 'cover', ...style }}
      />
    )
  }
  if (kind === 'custom' && /^(https?:\/\/|data:image\/|\/)/.test(normalized)) {
    return (
      <img
        alt=""
        className={className}
        src={normalized}
        style={{ width: size, height: size, objectFit: 'cover', ...style }}
      />
    )
  }
  // iconType='custom' + 有 cloud file id + cache 里 entry 还是 undefined →
  // **下载中**(AttachmentCache 区分:undefined=未尝试/in-flight,空串=已尝试
  // 但 fail/non-image,blob URL=已加载完成)。这种情况下不要先渲 sync fallback
  // 再切 img,中间帧会闪一下。返回透明占位,占住宽高不挪位。
  if (
    kind === 'custom' &&
    cloudFileId &&
    iconPreviewUrlByFileId &&
    iconPreviewUrlByFileId[cloudFileId] === undefined
  ) {
    return (
      <span
        aria-hidden
        style={{
          display: 'inline-block',
          width: size,
          height: size,
          ...style,
        }}
      />
    )
  }
  // iconType='custom' 但既没有 cloud blob 也不是合法 URL —— 大概率是 app 端
  // 上传时本地文件已经丢了(_uploadCategoryIcons 静默 skip),server 端
  // icon_cloud_file_id 留空,或者 cache 已经下载失败(空串)。这种情况下用
  // 'sync' 半透明字形作为"未同步"提示,跟默认 'category' 图标区分开。
  if (kind === 'custom') {
    return (
      <span
        aria-hidden
        title="custom icon not synced"
        className={`material-symbols-outlined ${className || ''}`.trim()}
        style={{
          fontSize: size,
          lineHeight: 1,
          color: color || 'currentColor',
          opacity: 0.45,
          fontVariationSettings: `'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' ${size}`,
          ...style,
        }}
      >
        sync
      </span>
    )
  }

  // 「可爱类别图示」画风:手绘 SVG 线稿 + 分类色底线,不画色底圆圈(圆圈由
  // 呼叫端自己的 wrapper 负责跳过,这里只管图示本身)。inline 渲染(而不是
  // `<img>`)是为了让 SVG 里的 `currentColor` 描边跟着 `color` 换色。
  if (iconStyle === 'cute') {
    const svg = resolveCuteIconSvg(normalized)
    return (
      <span
        className={className}
        style={{
          display: 'inline-flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
          ...style,
        }}
      >
        <span
          aria-hidden
          style={{ width: size, height: size, color: color || 'currentColor' }}
          // 素材是打包进 repo 的静态原创 SVG,不是使用者输入,可以放心 inline。
          dangerouslySetInnerHTML={{ __html: svg }}
        />
        {underlineColor ? (
          <span
            aria-hidden
            style={{
              width: Math.round(size * 0.6),
              height: 3,
              borderRadius: 2,
              background: underlineColor,
            }}
          />
        ) : null}
      </span>
    )
  }

  const name = resolveMaterialIconName(normalized)
  return (
    <span
      aria-hidden
      className={`material-symbols-outlined ${className || ''}`.trim()}
      style={{
        fontSize: size,
        lineHeight: 1,
        color: color || 'currentColor',
        // 这里显式重申 variation-settings,防止宿主页面有别的字体层级干扰。
        // opsz 必须跟 fontSize 对齐才能拿到最佳笔画粗细。
        fontVariationSettings: `'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' ${size}`,
        ...style,
      }}
    >
      {name}
    </span>
  )
}
