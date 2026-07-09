# 龙虎榜 GitHub Pages 动态看板

这是一个可以直接上传到 GitHub Pages 的静态网页项目。网页读取 `data/` 里的 JSON 数据，GitHub Actions 每天北京时间 18:00 自动运行一次 `update_tdx_daily.py`，拉取最新龙虎榜数据并提交回仓库。

## 重点规则

- 自动更新时间：北京时间每日 18:00。
- 龙虎榜明细来源：通达信龙虎榜接口。
- 收盘价来源：东方财富日 K 线接口。
- 收盘价口径：`fqt=0`，不复权真实收盘价。
- 不使用模拟数据、不估算、不倒推、不用涨跌幅反推收盘价。
- 如果某只股票当日收盘价没有被接口确认，前端只显示“收盘价未确认”。

## GitHub 仓库结构

上传到仓库根目录后，第一层应该直接看到：

```text
index.html
assets/
data/
update_tdx_daily.py
README.md
.nojekyll
.github/
  workflows/
    update-tdx-data.yml
```

不要再套一层多余文件夹，否则 GitHub Pages 可能打不开首页。

## GitHub Pages 设置

1. 打开仓库 `Settings`。
2. 进入 `Pages`。
3. Source 选择 `Deploy from a branch`。
4. Branch 选择 `main`，Folder 选择 `/ root`。
5. 保存后等待 GitHub 生成网址。

## Actions 权限设置

自动更新需要把 JSON 数据提交回仓库，所以要打开写入权限：

1. 打开仓库 `Settings`。
2. 进入 `Actions` → `General`。
3. 找到 `Workflow permissions`。
4. 选择 `Read and write permissions`。
5. 保存。

## 手动测试更新

上传后可以手动运行一次：

1. 打开仓库 `Actions`。
2. 选择 `Update TDX Daily Data`。
3. 点击 `Run workflow`。
4. 如果还没到 18:00，不建议强制运行；盘后测试可以保持 `force=false`。
5. 如果只是测试脚本是否能跑，可以把 `force` 选成 `true`。

## 输出数据

```text
data/latest.json              最新交易日完整数据
data/daily/YYYY-MM-DD.json    指定日期完整数据
data/index.json               日期索引
data/preselect-pool.json      预选池与 T+1/T+3/T+5/T+10 验证
```

## 说明

页面底部的“稳健预选池”只做复盘观察，不是投资建议。预选池收益率只在目标交易日真实收盘价确认后计算；没有确认前显示“等待”。
