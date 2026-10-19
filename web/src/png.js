// Minimal PNG decoder for the data textures (8-bit grey / grey+alpha / RGB / RGBA, non-interlaced), so the bytes
// reach the GPU and the CPU exactly as written: no colour management, gamma or premultiplication on the way.
// Inflate uses DecompressionStream; browsers without it fall back to createImageBitmap + canvas.

const SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
const CHANNELS = { 0: 1, 2: 3, 4: 2, 6: 4 };

async function inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

async function decodeNative(buffer) {
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < 8; i++) if (bytes[i] !== SIGNATURE[i]) throw new Error('not a PNG file');
  const view = new DataView(buffer);
  let pos = 8;
  let width = 0;
  let height = 0;
  let depth = 0;
  let type = 0;
  const idat = [];
  while (pos < bytes.length) {
    const len = view.getUint32(pos);
    const tag = String.fromCharCode(bytes[pos + 4], bytes[pos + 5], bytes[pos + 6], bytes[pos + 7]);
    const data = bytes.subarray(pos + 8, pos + 8 + len);
    if (tag === 'IHDR') {
      width = view.getUint32(pos + 8);
      height = view.getUint32(pos + 12);
      depth = data[8];
      type = data[9];
      if (data[12] !== 0) throw new Error('interlaced PNG is not supported');
    } else if (tag === 'IDAT') {
      idat.push(data);
    } else if (tag === 'IEND') {
      break;
    }
    pos += 12 + len;
  }
  const channels = CHANNELS[type];
  if (!channels || depth !== 8) throw new Error(`PNG colour type ${type} / bit depth ${depth} is not supported`);
  const raw = await inflate(new Blob(idat));
  const stride = width * channels;
  const out = new Uint8Array(stride * height);
  let src = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[src++];
    const row = y * stride;
    const prev = row - stride;
    for (let x = 0; x < stride; x++) {
      const v = raw[src++];
      const a = x >= channels ? out[row + x - channels] : 0;
      const b = y > 0 ? out[prev + x] : 0;
      const c = x >= channels && y > 0 ? out[prev + x - channels] : 0;
      let r;
      switch (filter) {
        case 0: r = v; break;
        case 1: r = v + a; break;
        case 2: r = v + b; break;
        case 3: r = v + ((a + b) >> 1); break;
        case 4: r = v + paeth(a, b, c); break;
        default: throw new Error(`bad PNG filter ${filter}`);
      }
      out[row + x] = r & 255;
    }
  }
  return { width, height, channels, data: out };
}

async function decodeCanvas(buffer) {
  const bitmap = await createImageBitmap(new Blob([buffer], { type: 'image/png' }), {
    premultiplyAlpha: 'none',
    colorSpaceConversion: 'none',
  });
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  const rgba = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  return { width: bitmap.width, height: bitmap.height, channels: 4, data: new Uint8Array(rgba.buffer) };
}

/** @returns {Promise<{width:number, height:number, channels:number, data:Uint8Array}>} rows top to bottom */
export async function decodePNG(buffer) {
  if (typeof DecompressionStream === 'function') return decodeNative(buffer);
  return decodeCanvas(buffer);
}

/** Expands 1-4 channel pixels to RGBA (missing alpha = 255). */
export function toRGBA({ width, height, channels, data }) {
  if (channels === 4) return data;
  const out = new Uint8Array(width * height * 4);
  for (let i = 0, j = 0; i < width * height; i++, j += channels) {
    const o = i * 4;
    if (channels >= 3) {
      out[o] = data[j];
      out[o + 1] = data[j + 1];
      out[o + 2] = data[j + 2];
    } else {
      out[o] = out[o + 1] = out[o + 2] = data[j];
    }
    out[o + 3] = channels === 2 ? data[j + 1] : 255;
  }
  return out;
}

/** First channel only (grey images, or the red channel). */
export function toRed({ width, height, channels, data }) {
  if (channels === 1) return data;
  const out = new Uint8Array(width * height);
  for (let i = 0; i < width * height; i++) out[i] = data[i * channels];
  return out;
}
