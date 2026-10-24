# 数据构建与交付

本仓库只负责把 [threathunterX/underground-slang-lexicon](https://github.com/threathunterX/underground-slang-lexicon)
当前 `main` 的词条与本地扩展合并，生成供
[jshdcn/anti-cheating-assistant](https://github.com/jshdcn/anti-cheating-assistant)
使用的 Markdown 文件。

## 输入与输出

- `local-data/local-entries.json`：本地补充词条与场景数据。
- `tools/merge_local.py`：拉取上游、合并数据并生成产物。
- `skill/underground-slang-lexicon.md`：合成词典。
- `skill/underground-slang-index.md`：按语种的词条索引，下游同步此文件。
- `skill/underground-slang-categories.md`：按分类和场景的独立索引，仅供本仓库按需查阅，不纳入下游同步。

产物记录所用上游版本和 CC BY 4.0 署名信息。上游数据不复制进本仓库；构建时需要联网访问 GitHub API。

## 构建与验证

**Build slang lexicon** 每 30 分钟自动运行一次：从上游 `threathunterX/underground-slang-lexicon` 的 `main` 拉取最新数据，运行合并场景测试并重建词典及两个索引。产物有变化时，workflow 会自动提交三个 Markdown 文件到本仓库的 `skill/`；随后 `jshdcn/anti-cheating-assistant` 每 5 分钟检查并拉取词典和按语种词条索引，不同步独立的分类 / 场景索引。上游更新通常会在约 30 分钟内开始处理，助手仓库再延迟最多约 5 分钟同步。

因此，**不需要点击 Sync fork** 来同步词典数据。GitHub 的 **Sync fork → Update branch** 是同步 fork 分支提交，不是本词典的数据刷新入口；这里的构建会直接读取上游最新数据。也可以在 GitHub Actions 页面手动运行 **Build slang lexicon** 立即检查；在 `main` 上成功后，有变化的产物会提交到 `skill/`，并作为 workflow artifact 保留 14 天。

也可以在仓库根目录本地运行：

```bash
python3 -m pip install -r requirements.txt
python3 tools/merge_local.py build
python3 tests/test_scenarios.py
```

每次构建都会读取上游 `main` 的最新数据，并使用本地扩展。定时任务、`main` 上的 push 和手动运行会在产物有变化时提交生成文件；Pull Request 只构建、验证并上传 artifact，不会改写仓库文件。

`tests/test_scenarios.py` 使用合成数据覆盖合并规则，不会访问真实上游或改写正式产物。
