# 林间小屋

Tiny Glade 风格的林中小屋微缩场景，面向手机的网页展示。点按时段图标或按住拖动调整时间，太阳和月亮实时移动，清晨、白天、黄昏、夜晚的光影、天空、雾、调色、窗灯和萤火虫随之变化。

在线页面：<https://evaqqwww.github.io/House/>（推送到 main 后由 GitHub Actions 自动构建发布）。

场景在 Blender 5.2 里由 Python 脚本程序化生成；烘焙脚本把天光、固定方向的太阳反弹光和灯光分层烘进顶点色和地形贴图，导出成网页资源；网页（three.js 0.186.1 WebGLRenderer + postprocessing 6.39.5，Vite 构建，纯 JavaScript）按时刻重组这些层，叠加实时的太阳和月亮。

## 目录

```
house.blend            Blender 场景（由 scripts/build_scene.py 生成）
scripts/               Blender 端：场景生成、时间模型、烘焙导出
  common.py            布局、调色板、地形高度函数、建模与材质工具
  build_terrain.py     地形、水塘、岩石、草和花（Geometry Nodes）
  build_house.py       小屋：瓦片、木板、石块逐块建模
  build_trees.py       树和灌木的变体、森林布置、网页用的三级 LOD 模型
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
npm test              # 检查点按、长按、拖动、取消和多指手势
npm run preview       # http://127.0.0.1:4173 预览 dist/
npm run preview:lan   # 局域网预览 dist/，手机实测用这个
```

手机访问电脑上的服务时，Windows 防火墙需要允许 Node.js 在专用网络上的入站连接。

首次加载约 6.9 MB：资源 5.9 MB，脚本 0.8 MB（gzip 后 0.2 MB）。

**发布**：推送到 main 分支后，`.github/workflows/pages.yml` 在 GitHub Actions 里执行 `npm ci` 和 `npm run build`，把 `web/dist` 发布到 GitHub Pages（仓库设置里 Pages 的来源为 GitHub Actions）。构建结果使用相对路径，所以能直接放在 `/House/` 子路径下。

### 页面

- **时间控制**：紧凑居中放在页面底部，平时只显示图标。点按四个图标，分别平滑过渡到 **清晨 07:00 / 白天 13:00 / 黄昏 17:24 / 夜晚 22:00**；长按或左右拖动时显示小型时间窗口，只显示当前时刻，松开后自动收起。向右滑时间前进（顺时针），向左滑时间后退（逆时针）。键盘聚焦时段图标后，方向键显示时间窗口并以 15 分钟、Page Up / Page Down 以 1 小时调整，Home / End 到 0 / 24 时，Escape 收起。
- **镜头**：在时间控制下方居中排列 **特写 / 总览 / 自由**。特写隐藏挡在镜头前的几棵树；自由模式可单指拖动旋转、双指捏合缩放和拖动平移，电脑上使用鼠标左键、滚轮、右键。在预设镜头下拖动画面直接进入自由模式。图标按钮可用 Tab 聚焦，按 Enter / 空格选择。
- **画质**：自动（默认）/ 低 / 标准 / 高。
- **帧率**：左上角显示 FPS（最近 120 帧的平均值，每秒刷新两次）、当前档位和渲染像素比，圆点绿色 ≥ 50 fps、黄色 ≥ 28 fps、红色更低；窄屏（≤ 400 px）只显示 FPS。点按它展开 / 收起详细调试面板。
- **自动隐藏**：5 秒无操作后，图标、画质、帧率和调试信息一起隐藏；再次点击画面恢复，这次点击只唤醒界面。长按、拖动和多指操作期间保持显示；加载与错误提示始终可见。隐藏时可按 Tab 聚焦唤醒区域，再按 Enter / 空格恢复。
- 竖屏和横屏都可用；图标按钮保留至少 32 × 44 px 的触控区，拖动时页面不滚动、不缩放，适配刘海屏安全区。
- 加载界面显示下载进度；加载错误、着色器错误和未捕获异常显示在错误层里。

| URL 参数 | 作用 |
| --- | --- |
| `?hour=17.5` | 初始时刻（0–24） |
| `?preset=closeup,night` | 初始镜头和 / 或时段：`overview` `closeup` `free`、`morning` `day` `dusk` `night`（也接受中文时段名） |
| `?quality=auto\|low\|medium\|high` | 画质（也接受 自动 / 低 / 标准 / 高） |
| `?debug` | 打开页面时就展开调试面板：FPS 与帧时间、GPU 帧时间（浏览器支持时）、渲染比例与分辨率、绘制调用与三角形、显存估算、阴影重画次数与代价、各级树的数量、草叶数、当前时刻 / 档位 / 镜头 |

### 画质档位

| | 低 | 标准 | 高 |
| --- | --- | --- | --- |
| 像素比上限 / 下限 | 1 / 0.5 | 1.5 / 0.6 | 2 / 0.75 |
| MSAA | 0 | 2 | 4 |
| 阴影贴图 / PCF 采样 | 1024² / 1 | 2048² / 2×2 | 4096² / 4×4 |
| 景深 | 关 | ¼ 像素，20 采样 | ¼ 像素，40 采样 |
| 泛光取高光的分辨率 | ¼ | ½ | ½ |
| 草叶 | 25 000 | 60 000 | 200 000 |
| 树的三角形上限 | 70 k | 170 k | 450 k |
| 萤火虫 | 120 | 250 | 400 |
| 风动 | 关 | 开 | 开 |

- **自动**：手机和平板选标准，弱 GPU 或内存 / 核数少的手机选低；电脑选高，集成显卡选标准。
- **动态分辨率**：按帧时间在像素比上下限之间调整，手机以 30 fps、电脑以 60 fps 为目标。
- 标准档的预算：任一预设镜头约 53 万三角形、45–60 次绘制、贴图和渲染目标约 56 MB。

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

### 技术取舍

混合烘焙与实时光照让静态小屋保留接触阴影、多次反弹和局部暖光，同时用实例化、三级树木 LOD、按需阴影和动态分辨率控制手机端成本。限制是烘焙层依赖静态几何；树冠用逐顶点半球光、透光和 L2 阴影代理近似；反弹层只在默认太阳方向烘焙，日月移动时只缩放其颜色和强度。

改变灯光功率、位置或分布后需重新烘焙 Lp：仅改玻璃自发光或实时树木点光不会同步改变墙面、地面暖光。网页只读取 `manifest.timeOfDay`，修改源时间模型后也须同步导出清单，保持模型、烘焙层和解码系数配套。

## 时间模型

`scripts/timeofday.json` 是唯一的参数来源，`timeofday.py`（Blender）和 `web/src/timeofday.js`（网页）的求值完全一致，`manifest.timeOfDay` 是它的副本。

- 关键帧按小时线性插值，24 时回绕。每个关键帧给出太阳强度和颜色、月光强度、天空天顶 / 地平线颜色和强度、雾的颜色和浓度、曝光、灯光（0–1）、泛光、饱和度、萤火虫、色调（tint）、夜色调整强度（nightShift）。
- `sunPath`：5:30 日出、18:30 日落，太阳方位角从 40° 匀速转到 −200°，最高 46°；夜里高度角为负，太阳关闭。月亮方向和颜色固定。
- `presets`：清晨 7、白天 13、黄昏 17.4、夜晚 22；`defaultHour` 为 9。
- 夜色调整：先将像素原色逐通道乘 `nightGrade.color`，再归一化回原来的 Rec.709 亮度，保留草绿、泥土与屋瓦之间的颜色差异。暖色保护量由 `(r − max(g, b)) / max(Rec.709 亮度, 1e-5)` 在 `warmth` 范围 0.12–0.65 内平滑增加；夜色混合权重同时乘暗部遮罩和暖色保护的反值。Blender 合成器与网页后期使用相同规则。
- `skyDome`：看得见的天空（相机和反射看到的天空球）的颜色，按小时给天顶和地平线两色，和关键帧一样线性插值；照亮场景的天光用关键帧的 `skyZenith` / `skyHorizon`。白天是平涂的青蓝（天顶 #36879a），夜里是较暗的藏蓝，往地平线变亮；黎明经紫、桃、暖白到青，黄昏经金、橙、玫瑰、薰衣草回到藏蓝。天空球从地平线色到天顶色按 t^`curve`（0.5）混合，暖色集中在地平线一带。后期对天空只加泛光和暗角，再做视图变换（跳过雾、夜色调整、色调、饱和度和曝光）；显示颜色由这里的线性值经 AgX 得到。

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
| Lp | 灯笼、窗玻璃、灯笼点光源及窗外柔光全开时的光照，存为与灯光颜色的亮度比 |
| Z | 天顶比例：表面看到的天空里天顶色所占的权重（朝上的平地接近天顶色，墙和岩面偏地平线色） |

太阳和月亮里照亮平地更多的一个是主光，独占阴影贴图。

| 物体 | 着色 |
| --- | --- |
| 小屋、岩石、花、睡莲叶、芦苇 | 上式；烘焙层在顶点色里，阴影逐像素；窗玻璃和灯罩有随时间变化的自发光 |
| 地形 | 上式，烘焙层取自地形贴图；有草的地方按等效的“草地模型”着色，叠加条纹噪声 |
| 树和灌木 | 逐顶点：球面转移法线的 Lambert、阴影、天空 / 地面半球光、叶间散射、叶片透光与逆光辉光、夜里灯笼的实时点光源 |
| 草叶 | 浏览器里按 grass.png 的密度用固定种子生成，每片 3 个三角形；光照取根部的烘焙层和阴影 |
| 水面 | 深度色 × 地形光照 + 当前天空球颜色（`skyDome`）的菲涅尔反射 + 日 / 月光斑 |
| 天空 | `skyDome` 的天空球颜色，与 Blender 相机看到的天空相同（Blender 世界节点里相机和反射光线看天空球颜色，漫反射光照看天光渐变）；水面反射同一天空；夜里加星星和月亮 |
| 烟、萤火虫 | 烟随当前光照着色；弯折、鼓包和疏密随烟气一起上升，越往上摆得越大，阵风把烟柱吹向下风，顶部散成丝缕，边缘淡出；萤火虫是草地上空闪烁游动的加法混合点，只在夜里出现 |

### 夜景调整

夜景的主要问题来自偏弱的月光和环境照明、过亮的可见天空，以及只按亮度判断的夜色调整把暗处暖光也染蓝。调整以可辨认的钴蓝树冠、琥珀色小屋和前中后景层次为目标：

- 月光方位 / 高度为 −110° / 48°，深夜强度为 1.65；环境照明弱于月光受光面，可见天空为深蓝紫，用侧光、树冠明暗和远景雾区分层次。
- 灯色使用较低饱和度的暖白，线性 RGB `[1, 0.52, 0.32]`；灯笼点光功率 220 W，窗玻璃 / 灯罩自发光为 2.1 / 14，保持柔和暖色。灯光层按正式的 1024 采样烘焙；树冠点光增益为 0.24。
- 夜色调整使用上述保留材质色彩、归一化亮度的规则和暖色保护，不再把暗部统一替换成蓝色。网页叶片的夜间冷光散射使用 `[0.75, 0.8, 3.3]` 响应，实体表面的冷光使用 `[1.1, 0.8, 0.7]` 响应，区分宝蓝树冠与蓝紫灰屋瓦；它们只影响冷光，白天、灯光层与自发光保持原响应。萤火虫核心亮度为 4.5，保留细小暖色点缀。以上调整不增加绘制通道或阴影贴图。
- 网页地表与草叶共用夜间冷光系数 0.22，草地再乘 `[0.55, 0.68, 0.95]` 的墨绿冷光响应，同时压低月光、天光、反弹及草叶透光。地面使用独立的 `uGroundNight = 1 − smoothstep(−3°, 6°, 太阳高度)`，先于强月光完成平滑过渡，避免约 19 时仍带白天响应而先变亮、后变暗；树冠、房屋与后期继续使用原来的夜色进度。夜间天空 / 直射响应使用 `ground_cover.png` 的原始覆盖率，与树冠下减少草叶后的密度分开；冷光底色在树冠稀疏处恢复约六成半原地表色（已有静态团簇变化使其在 55.25%–74.75% 间变化），明显减弱铺满草毯的感觉，保留自然疏密；白天地表与草叶生成仍使用原有密度。裸露泥土、湿岸和高台坡面也使用 0.22 的夜间冷光系数，保留原有材质色，避免低草覆盖率处退回全强度蓝光而形成亮边。
- 草地暖光使用固定的 `[0.39, 0.252, 0.32]` 反射校正；地表暖光底色共用上述树下原地表色恢复，光照覆盖仍使用原始地表覆盖率，只有光源强度随时间渐入，避免一遇暖光又变成整片草毯，以及草地的夜色增益与灯光渐入相乘后产生短暂亮斑或树下光斑变形。门前覆盖率与草叶密度一致，暖光过渡保持原样，门窗、墙面和灯光范围不受影响。
- 太阳在高度 1.5° 至 0° 间平滑淡出，网页直射、反弹和主阴影光源的选择使用同一有效强度，Blender 太阳能量同步采用相同曲线，消除地平线处的突然关断；黄昏预设与深夜状态保持一致。
- 清晨月光在 04:36 至 05:30 间渐退到零，随后由已有的太阳地平线渐入接管；主阴影方向在两种直射光都为零时切换，避免约 05:46 残余月光与晨光交接时树下阴影及草叶透光跳变。门口暖光沿原有关键帧渐退，深夜与黄昏效果不变。
- 墙面窗外矩形柔光的宽 / 高为窗洞的 1.6 / 1.3 倍，朝外略向下（下降量 / 向外距离 = 0.2），功率按窗面积 × 65 W/m²；山墙阁楼窗再乘 0.2，屋顶老虎窗仅保留玻璃发光，减轻屋顶光斑。灯笼半径为 0.5 m，柔化门前阴影边缘。这些光只进入 Blender 渲染与 Lp 烘焙，不增加网页实时光源；所有局部灯光在天空、反弹和天顶层烘焙时关闭，避免混入白天照明。

## 渲染

- **阴影**：一张方向光阴影贴图，只在主光方向变化或投影体变化（特写镜头隐藏的树）时重画，场景静止时不重画。投影体是小屋、岩石、花、地形和所有树的最低一级代理模型。
- **树的细节层级**：每个变体有 L0 / L1 / L2 三级（约 3000 / 1000 / 300 三角形），按距离和屏幕像素密度逐棵选择，逐棵视锥剔除；超过档位的三角形上限时从最远的树开始降级。自由模式隐藏挡住视线的树。
- **后期**：线性 HDR → 泛光 → 一个合成通道：景深，然后按 Blender 合成器的顺序依次加泛光、雾（按深度）、夜色调整、色调、饱和度、暗角、曝光，最后做 Blender 的 AgX 视图变换（Look 为 Base Contrast，用 `src/agx-lut.png` 查表）。天空球跳过雾、夜色调整、色调、饱和度和曝光：天空球写 alpha 0、其余物体写 1（透明物写各自的覆盖），多重采样解析后在边缘是覆盖率，场景与天空按它混合；开景深时这一步在模糊之前做（清晰像素和模糊用的预滤波都先调色），模糊的边缘混的是调好色的颜色。Blender 用透明胶片和 Environment 通道做同样的拆分。景深直接用 Blender 相机的焦距、光圈和对焦距离，在 ¼ 像素的缓冲上计算。

## 网页资源（`web/public/assets/`）

| 文件 | 内容 |
| --- | --- |
| `scene.glb` | +Y 向上，EXT_meshopt_compression + KHR_mesh_quantization。节点 `extras.shade`：`lit`（COLOR_0 = 反照率；COLOR_1 = √(S/skyScale)，a 为 √(Lp/lampScale)；COLOR_2 = √(B/bounceScale)，a 为 Z）、`terrain`（反照率 + 平面 UV）、`treeLOD`（`<变体>_L0/_L1/_L2`）、`water`、`smoke`；相机 CAM_Overview / CAM_Closeup 的 extras 带 `lens_mm`、`sensor_mm`、`fstop`、`target` |
| `terrain_sky.png` | 1024²，√(S/skyScale) |
| `terrain_bounce.png` | 512²，√(B/bounceScale) |
| `terrain_lamp.png` | 512² 灰度，√(Lp/lampScale) |
| `terrain_zenith.png` | 512² 灰度，Z |
| `ground_albedo.png` | 512²，草下地面颜色（sRGB） |
| `ground_cover.png` | 512² 灰度，树冠稀疏处理前的地表覆盖率，控制地面夜间冷光响应 |
| `grass.png` | 512²：R = 草密度，G/B = 16 位地形高度 |
| `manifest.json` | 资源版本（2）、各层的解码系数、草和树的参数与 374 棵树的实例、时间模型、灯笼位置、雾、相机目标与特写走廊、文件大小 |

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
forest.js        树的实例、逐棵 LOD 与剔除、特写走廊、阴影代理
cameras.js       预设镜头、自由轨道相机、过渡、横竖屏视野
post.js          泛光、景深和合成通道
viewlut.js       AgX 查找表的加载与查找
agx-lut.png      Blender AgX（Base Contrast，sRGB）的 44³ 查找表
quality.js       画质档位、设备检测、动态分辨率
gputimer.js      GPU 帧计时（调试面板用）
ui.js            加载界面、错误层、时段图标与时间窗口、镜头按钮、FPS 角标、调试面板
style.css
materials/       lit / terrain / trees / grass / water / smoke / sky / fireflies
```
