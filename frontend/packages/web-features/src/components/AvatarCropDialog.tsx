import { useEffect, useState } from 'react'
import Cropper, { type Area } from 'react-easy-crop'

import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, useT } from '@beecount/ui'

import { cropImageToFile } from '../lib/cropImage'

/** 裁剪來源:剛選好還沒上傳的原始檔案,或是既有頭像的 blob URL(重新編輯
 *  已上傳過的圖片時用 —— 這種情況沒有原始檔案可用,直接拿目前的預覽圖再裁一次)。 */
export type AvatarCropSource = File | { url: string }

export type AvatarCropDialogProps = {
  /** 傳 null 代表關閉。 */
  source: AvatarCropSource | null
  /** 裁剪框長寬比,例如帳戶頭像 4/3、個人頭像 1。 */
  aspect: number
  /** 裁剪框形狀,對應輸出圖片會被套用的顯示形狀(圓形頭像 vs 矩形卡片)。 */
  cropShape?: 'rect' | 'round'
  onCancel: () => void
  onConfirm: (croppedFile: File) => void | Promise<void>
}

/** 頭像上傳前的裁剪彈窗 —— 帳戶頭像(AccountsPanel)與個人資料頭像
 *  (SettingsProfileAppearanceSection)共用,裁剪比例/形狀由呼叫方指定。 */
export function AvatarCropDialog({
  source,
  aspect,
  cropShape = 'rect',
  onCancel,
  onConfirm
}: AvatarCropDialogProps) {
  const t = useT()
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [crop, setCrop] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<Area | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!source) {
      setImageSrc(null)
      return
    }
    setCrop({ x: 0, y: 0 })
    setZoom(1)
    setCroppedAreaPixels(null)
    if (source instanceof File) {
      const url = URL.createObjectURL(source)
      setImageSrc(url)
      return () => URL.revokeObjectURL(url)
    }
    // 既有 blob URL 由外部(AttachmentCache)持有生命週期,這裡不 revoke。
    setImageSrc(source.url)
    return undefined
  }, [source])

  const handleConfirm = async () => {
    if (!imageSrc || !croppedAreaPixels) return
    setSubmitting(true)
    try {
      const croppedFile = await cropImageToFile(imageSrc, croppedAreaPixels, 'avatar.jpg')
      await onConfirm(croppedFile)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={!!source} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('avatarCrop.title')}</DialogTitle>
        </DialogHeader>

        <div className="relative h-72 w-full overflow-hidden rounded-md bg-black/80">
          {imageSrc ? (
            <Cropper
              image={imageSrc}
              crop={crop}
              zoom={zoom}
              aspect={aspect}
              cropShape={cropShape}
              showGrid={cropShape === 'rect'}
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={(_area, areaPixels) => setCroppedAreaPixels(areaPixels)}
            />
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{t('avatarCrop.zoom')}</span>
          <input
            type="range"
            min={1}
            max={3}
            step={0.01}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            className="flex-1"
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => void handleConfirm()} disabled={submitting || !croppedAreaPixels}>
            {t('common.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
