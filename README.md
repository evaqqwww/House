# 林间小屋

Tiny Glade 风格的林中小屋微缩场景，面向手机的网页展示。拖动时间滑块，太阳和月亮实时移动，白天、黄昏、夜晚的光影、天空、雾、调色、窗灯和萤火虫随之变化。

在线页面：<https://evaqqwww.github.io/House/>（推送到 main 后由 GitHub Actions 自动构建发布）。

场景在 Blender 5.2 里由 Python 脚本程序化生成；烘焙脚本把与太阳位置无关的光照（天光、太阳反弹光、灯光）烘进顶点色和地形贴图，导出成网页资源；网页（three.js 0.186.1 WebGLRenderer + postprocessing 6.39.5，Vite 构建，纯 JavaScript）在运行时叠加实时的太阳和月亮。

## 目录

```
house.blend            Blender 场景（由 scripts/build_scene.py 生成）
textures/
  leaf_clusters.png    手绘叶团图集（2×2 格，灰度，硬 alpha），export_web.py 由它生成 leaves.png
scripts/               Blender 端：场景生成、时间模型、烘焙导出
  common.py            布局、调色板、地形高度函数、建模与材质工具
  build_terrain.py     地形、水塘、岩石、草和花（Geometry Nodes）
  build_house.py       小屋：瓦片、木板、石块逐块建模
  build_trees.py       树和灌木的变体、森林布置、网页用的三级 LOD 模型（树干、叶团内核、叶卡）和投影外壳
  build_lighting.py    太阳、月亮、灯笼点光源、天空、相机、渲染与合成器设置
  build_scene.py       依次运行以上模块，保存 house.blend
  timeofday.json       时间模型（Blender 和网页共用）
  timeofday.py         时间模型求值，并应用到 Blender 场景
  export_web.py        烘焙光照层、导出网页资源
web/                   网页
  index.html
  vite.config.js
  src/                 网页源码（见“网页源码”）
  scripts/
    optimize-glb.mjs   导出后重新压缩 scene.glb
    make-agx-lut.py    生成 src/agx-lut.png
  public/assets/       网页资源（由 export_web.py 生成）
.github/workflows/
  pages.yml            构建网页并发布到 GitHub Pages
```

## 网页

```bash
cd web
npm install
npm run dev           # http://127.0.0.1:5173
npm run dev:lan       # 监听 0.0.0.0:5173，同一 Wi-Fi 下的手机打开 http://<电脑局域网 IP>:5173
npm run build         # 输出到 dist/（相对路径，可放到任意静态服务器的子目录）
npm run preview       # http://127.0.0.1:4173 预览 dist/
npm run preview:lan   # 局域网预览 dist/，手机实测用这个
```

手机访问电脑上的服务时，Windows 防火墙需要允许 Node.js 在专用网络上的入站连接。

首次加载约 6.9 MB：资源 6.0 MB，脚本 0.8 MB（gzip 后 0.2 MB）。

**发布**：推送到 main 分支后，`.github/workflows/pages.yml` 在 GitHub Actions 里执行 `npm ci` 和 `npm run build`，把 `web/dist` 发布到 GitHub Pages（仓库设置里 Pages 的来源为 GitHub Actions）。构建结果使用相对路径，所以能直接放在 `/House/` 子路径下。

### 页面

- **时间滑块**：0–24 时，显示时刻和时段。**白天 / 黄昏 / 夜晚** 平滑过渡到 13:00 / 17:24 / 22:00。
- **镜头**：总览、特写（隐藏挡在镜头前的几棵树）、自由（单指拖动旋转，双指捏合缩放、拖动平移；电脑上鼠标左键、滚轮、右键）。在预设镜头下拖动画面直接进入自由模式。
- **画质**：自动（默认）/ 低 / 标准 / 高。
- **帧率**：左上角常驻显示 FPS（最近 120 帧的平均值，每秒刷新两次）、当前档位和渲染像素比，圆点绿色 ≥ 50 fps、黄色 ≥ 28 fps、红色更低；窄屏（≤ 400 px）只显示 FPS。点按它展开 / 收起详细调试面板。
- 竖屏和横屏都可用；按钮触控区 ≥ 40 px，拖动时页面不滚动、不缩放，适配刘海屏安全区。
- 加载界面显示下载进度；加载错误、着色器错误和未捕获异常显示在错误层里。

| URL 参数 | 作用 |
| --- | --- |
| `?hour=17.5` | 初始时刻（0–24） |
| `?preset=closeup,night` | 初始镜头和 / 或时段：`overview` `closeup` `free`、`day` `dusk` `night` |
| `?quality=auto\|low\|medium\|high` | 画质（也接受 自动 / 低 / 标准 / 高） |
| `?debug` | 打开页面时就展开调试面板：FPS 与帧时间、GPU 帧时间（浏览器支持时）、渲染比例与分辨率、绘制调用与三角形、显存估算、阴影重画次数与代价、各级树的数量、草叶数、叶卡数、当前时刻 / 档位 / 镜头 |

### 画质档位

| | 低 | 标准 | 高 |
| --- | --- | --- | --- |
| 像素比上限 / 下限 | 1 / 0.5 | 1.5 / 0.6 | 2 / 0.75 |
| MSAA | 0 | 2 | 4 |
| 阴影贴图 / PCF 采样 | 1024² / 1 | 2048² / 2×2 | 4096² / 4×4 |
| 景深 | 关 | ¼ 像素，20 采样 | ¼ 像素，40 采样 |
| 泛光取高光的分辨率 | ¼ | ½ | ½ |
| 草叶 | 25 000 | 60 000 | 200 000 |
| 树的三角形上限 | 60 k | 70 k | 180 k |
| 叶卡上限 | 10 000 | 20 000 | 40 000 |
| 叶卡边缘 | alpha 测试 | alpha-to-coverage | alpha-to-coverage |
| 萤火虫 | 120 | 250 | 400 |
| 风动 | 关 | 开 | 开 |

- **自动**：手机和平板选标准，弱 GPU 或内存 / 核数少的手机选低；电脑选高，集成显卡选标准。
- **动态分辨率**：按帧时间在像素比上下限之间调整，手机以 30 fps、电脑以 60 fps 为目标。
- 标准档的预算：任一预设镜头约 43 万三角形、67–81 次场景绘制、至多 2 万张叶卡、贴图和渲染目标约 61 MB。

## Blender 管线

Blender 5.2.2，路径 `D:/Program Files/Blender Foundation/Blender 5.2/blender.exe`。

**生成场景**（写入 `house.blend`）。在 Blender 的 Python 控制台里：

```python
import sys; sys.dont_write_bytecode = True; sys.path.insert(0, r"D:\House\scripts")
import build_scene; build_scene.build()
```

或者后台运行：

```bash
"D:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b D:/House/house.blend --python-expr "import sys; sys.dont_write_bytecode = True; sys.path.insert(0, r'D:/House/scripts'); import build_scene; build_scene.build()"
```

`build_scene.build(parts=["build_lighting"])` 只重建其中几部分。每个 `build_*` 模块的 `build()` 先清空自己的集合再重建，结果完全确定（随机数都有固定种子）。

**调整时刻**：`import timeofday; timeofday.apply(17.4)` 把太阳、月亮、天空、灯光、雾、泛光、夜色调整和曝光切到该时刻，用 Cycles 渲染就能看到那个时刻的参考效果。

**导出网页资源**（后台运行，不修改 house.blend，RTX 4080 SUPER 上约 2 分钟）：

```bash
"D:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b D:/House/house.blend -P D:/House/scripts/export_web.py
```

参数：`--samples N`（每次烘焙的采样数，默认 1024）、`--quick`（开发用：32 采样、更宽的去噪模糊）、`--out DIR`（默认 `web/public/assets`）。导出结束时自动运行 `web/scripts/optimize-glb.mjs`（需要先在 web 里 `npm install`）。

**视图变换查找表**：Blender 升级或颜色管理配置变化时重新生成 `web/src/agx-lut.png`：

```bash
"D:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b --factory-startup -P D:/House/web/scripts/make-agx-lut.py
```

## 时间模型

`scripts/timeofday.json` 是唯一的参数来源，`timeofday.py`（Blender）和 `web/src/timeofday.js`（网页）的求值完全一致，`manifest.timeOfDay` 是它的副本。

- 关键帧按小时线性插值，24 时回绕。每个关键帧给出太阳强度和颜色、月光强度、天空天顶 / 地平线颜色和强度、雾的颜色和浓度、曝光、灯光（0–1）、泛光、饱和度、萤火虫、色调（tint）、夜色调整强度（nightShift）。
- `sunPath`：5:30 日出、18:30 日落，太阳方位角从 40° 匀速转到 −200°，最高 46°；夜里高度角为负，太阳关闭。月亮方向和颜色固定。
- `presets`：白天 13、黄昏 17.4、夜晚 22；`defaultHour` 为 9。
- 夜色调整：越暗的地方越往亮度不变的蓝色靠（`nightGrade`），灯光照亮的亮处保留原色，所以月夜是蓝的而窗光和灯笼是暖的。

坐标：Blender 的 (x, y, z) 对应 three.js 的 (x, z, −y)；方位角从 +X 逆时针量，光从该方向照来。

## 光照

光照单位 L：radiance = albedo × L，全部为线性值。

```
radiance = albedo · ( 太阳 · max(N·L☉, 0) · 阴影 + 月亮 · max(N·L☾, 0)      实时
                    + skyRad(Z) · S + bounce · B + lamp · lampColor · Lp )     烘焙层
         + emission

太阳      = sunStrength · sunColor / π（太阳在地平线下为 0）
skyRad(Z) = skyStrength · mix(skyHorizon, skyZenith, Z)
bounce    = 按当前日 / 月照亮地面的程度（高度角正弦）缩放的太阳反弹
emission  = 窗玻璃和灯罩随 lamp 变化的自发光
```

烘焙层（`export_web.py`，Cycles 漫反射光照通道，烘焙时不含草和烟）：

| 层 | 内容 |
| --- | --- |
| S | 均匀白色天空（辐亮度 1）下的光照，含多次反弹；开阔平地约为 1 |
| B | 默认时刻方向、强度 1 的白色太阳的间接光（反弹光） |
| Lp | 灯笼、窗玻璃和灯笼点光源全开时的光照，存为与灯光颜色的亮度比 |
| Z | 天顶比例：表面看到的天空里天顶色所占的权重（朝上的平地接近天顶色，墙和岩面偏地平线色） |

太阳和月亮里照亮平地更多的一个是主光，独占阴影贴图。

| 物体 | 着色 |
| --- | --- |
| 小屋、岩石、花、睡莲叶、芦苇 | 上式；烘焙层在顶点色里，阴影逐像素；窗玻璃和灯罩有随时间变化的自发光 |
| 地形 | 上式，烘焙层取自地形贴图；有草的地方按等效的“草地模型”着色，叠加条纹噪声 |
| 树和灌木 | 逐顶点：球面转移法线的 Lambert、阴影、天空 / 地面半球光、叶间散射、叶片透光与逆光辉光、夜里灯笼的实时点光源。叶卡每个角用向该角弯曲的树冠法线照明，叶团根部略暗；反照率 = 顶点色 × leaves.png 的灰度 ÷ 图集平均灰度 |
| 草叶 | 浏览器里按 grass.png 的密度用固定种子生成，每片 3 个三角形；光照取根部的烘焙层和阴影 |
| 水面 | 深度色 × 地形光照 + 当前天空渐变的菲涅尔反射 + 日 / 月光斑 |
| 天空 | 与 Blender 天空相同的渐变，夜里加星星和月亮 |
| 烟、萤火虫 | 烟随当前光照着色；萤火虫是草地上空闪烁游动的加法混合点，只在夜里出现 |

## 渲染

- **阴影**：一张方向光阴影贴图，只在主光方向变化或投影体变化（特写镜头隐藏的树）时重画，场景静止时不重画。投影体是小屋、岩石、花、地形和每棵树的不透明外壳（`<变体>_Shadow`，树叶沿树冠法线外推）。
- **树叶**：Tiny Glade 式的叶团卡片。每级模型由树干、暗色的低模叶团内核（不透明，挡住卡片之间的缝）和叶卡组成。叶卡在顶点着色器里围绕卡片中心展开成朝向相机的方片，贴 leaves.png 图集里随机一格（随机水平镜像）；图集的“上”（从叶团根部指向叶尖）转向树冠法线在屏幕上的方向并略向上偏，所以轮廓上的叶团向外张开、正面的叶团朝上，再加每张卡自己的随机旋转。背向相机的卡片在内核后面，随角度逐渐缩小到不画。风吹动整张卡片。每帧先画所有树的树干和内核（由近到远），再画叶卡，最后画场景其余部分，被挡住的卡片和地面在深度测试时就被剔除。
- **树的细节层级**：每个变体有 L0 / L1 / L2 三级（树 300 / 110 / 40 张叶卡、约 920 / 430 / 180 三角形；灌木 70 / 30 / 12 张叶卡），按距离和屏幕像素密度逐棵选择，逐棵视锥剔除；超过档位的三角形或叶卡上限时从最远的树开始降级。自由模式隐藏挡住视线的树。
- **后期**：线性 HDR → 泛光 → 一个合成通道：景深，然后按 Blender 合成器的顺序依次加泛光、雾（按深度）、夜色调整、色调、饱和度、暗角、曝光，最后做 Blender 的 AgX 视图变换（Look 为 Base Contrast，用 `src/agx-lut.png` 查表）。景深直接用 Blender 相机的焦距、光圈和对焦距离，在 ¼ 像素的缓冲上计算。

## 网页资源（`web/public/assets/`）

| 文件 | 内容 |
| --- | --- |
| `scene.glb` | +Y 向上，EXT_meshopt_compression + KHR_mesh_quantization。节点 `extras.shade`：`lit`（COLOR_0 = 反照率；COLOR_1 = √(S/skyScale)，a 为 √(Lp/lampScale)；COLOR_2 = √(B/bounceScale)，a 为 Z）、`terrain`（反照率 + 平面 UV）、`treeLOD`（`<变体>_L0/_L1/_L2`，图元 M_Bark 树干、M_LeafCore 叶团内核、M_LeafCards 叶卡：每张卡 4 个顶点共用卡片中心为 POSITION，NORMAL 为卡片中心的树冠法线，COLOR_0 为反照率，TEXCOORD_0 为该角的图集 UV，`_CARD` = (角 x, 角 y（+1 指向叶尖）, 半边长 m, 随机旋转 rad)，浮点不量化）、`treeShadow`（`<变体>_Shadow`，投影用的不透明外壳）、`water`、`smoke`；相机 CAM_Overview / CAM_Closeup 的 extras 带 `lens_mm`、`sensor_mm`、`fstop`、`target` |
| `terrain_sky.png` | 1024²，√(S/skyScale) |
| `terrain_bounce.png` | 512²，√(B/bounceScale) |
| `terrain_lamp.png` | 512² 灰度，√(Lp/lampScale) |
| `terrain_zenith.png` | 512² 灰度，Z |
| `ground_albedo.png` | 512²，草下地面颜色（sRGB） |
| `grass.png` | 512²：R = 草密度，G/B = 16 位地形高度 |
| `leaves.png` | 1024² RGBA 叶团图集（2×2 格），由 `textures/leaf_clusters.png` 生成：sRGB 灰度，直通 alpha（源图的硬 alpha 经轻微高斯软化，0.5 处裁切，放大时边缘平滑），叶子颜色向透明区外扩 48 像素、再往外为平均灰度，mipmap 不会把黑色带进叶缘 |
| `manifest.json` | 资源版本（2）、各层的解码系数、草和树的参数与 374 棵树的实例、叶团图集参数（`trees.leafAtlas`：格数、卡片显示的居中比例、不透明像素的线性平均灰度、alpha 阈值）、时间模型、灯笼位置、雾、相机目标与特写走廊、文件大小 |

所有地形贴图用同一套平面映射 u = (x + 55) / 110、v = (z + 55) / 110（three.js 坐标），PNG 第一行是 v = 0，由 `src/png.js` 直接解码，不经过浏览器的色彩管理。

## 网页源码（`web/src/`）

```
main.js          启动、URL 参数、档位、帧循环
assets.js        加载 manifest、GLB 和地形贴图，显示下载进度
png.js           PNG 解码（数据贴图保持原值）
timeofday.js     时间模型求值
lighting.js      按时刻更新所有材质共用的光照参数
shadows.js       主光阴影贴图：投影体、视锥拟合、按需重画
shaderlib.js     共用 GLSL：光照、阴影查找、噪声、草地模型
world.js         按 extras.shade 组装场景、材质、阴影、树、草、天空、萤火虫
forest.js        树的实例（树干 + 内核、叶卡各一组）、逐棵 LOD 与剔除、三角形和叶卡预算、特写走廊、投影外壳
cameras.js       预设镜头、自由轨道相机、过渡、横竖屏视野
post.js          泛光、景深和合成通道
viewlut.js       AgX 查找表的加载与查找
agx-lut.png      Blender AgX（Base Contrast，sRGB）的 44³ 查找表
quality.js       画质档位、设备检测、动态分辨率
gputimer.js      GPU 帧计时（调试面板用）
ui.js            加载界面、错误层、时间滑块、按钮、FPS 角标、调试面板
style.css
materials/       lit / terrain / trees / grass / water / smoke / sky / fireflies
```
