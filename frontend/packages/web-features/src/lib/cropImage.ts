export type CropPixels = { x: number; y: number; width: number; height: number }

const MAX_OUTPUT_SIDE = 800
const OUTPUT_QUALITY = 0.85

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('image_load_failed'))
    img.src = src
  })
}

/** 依 react-easy-crop 的 croppedAreaPixels 把來源圖裁切出來,若最長邊超過
 *  MAX_OUTPUT_SIDE 會等比縮小,輸出成 JPEG(quality 0.85)包成 File。 */
export async function cropImageToFile(
  imageSrc: string,
  cropPixels: CropPixels,
  fileName: string
): Promise<File> {
  const image = await loadImage(imageSrc)

  const scale = Math.min(1, MAX_OUTPUT_SIDE / Math.max(cropPixels.width, cropPixels.height))
  const outputWidth = Math.max(1, Math.round(cropPixels.width * scale))
  const outputHeight = Math.max(1, Math.round(cropPixels.height * scale))

  const canvas = document.createElement('canvas')
  canvas.width = outputWidth
  canvas.height = outputHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('canvas_context_unavailable')

  ctx.drawImage(
    image,
    cropPixels.x,
    cropPixels.y,
    cropPixels.width,
    cropPixels.height,
    0,
    0,
    outputWidth,
    outputHeight
  )

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, 'image/jpeg', OUTPUT_QUALITY)
  )
  if (!blob) throw new Error('canvas_export_failed')

  return new File([blob], fileName, { type: 'image/jpeg' })
}
