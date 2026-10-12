# 仓库页签覆盖层用到的模板（M44）

`DepotAllTabClicked.png`：仓库「全部」页签的**选中态**截图（roi `[450,0,300,138]`，1280×720
坐标，取自模拟器 2560×1440 截图 ×0.5 裁切）。内核自带 `template/Depot/` 下只有
`DepotAllTab.png`（未选中）与 `DepotMaterialTabClicked.png`（养成材料选中），缺「全部选中」
这一张，而 M44 的覆盖层要在点完「全部」后确认页签已切换，所以自带一张。

**为什么放在仓库里而不是 `resource/`**：`resource/` 不进版本库（AGENTS.md）。覆盖层由启动
时生成（照 M38 `ensure_task_delay` 的做法）：把任务覆盖 JSON 与这张模板拷进
`resource/maa-layers/custom/resource/`，内核加载资源时生效。

换机/换分辨率时要重新裁一张：进仓库 → 切到「全部」页签 → 截图 → 按 `[900,0,600,276]`
（2560×1440 下）裁切。
