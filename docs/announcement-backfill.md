# 按公告日期手动补抓 arXiv

独立 workflow：`.github/workflows/backfill.yml`，名称为 **arXiv manual announcement backfill**。
只有 `workflow_dispatch` 手动入口，不需要运行每日任务，也不会修改每日任务的 workflow、爬虫或去重脚本。

## 使用方法

1. 将本功能合并到仓库默认分支，让 GitHub 显示手动运行入口。
2. 确保 Linux self-hosted runner 在线，可以访问 arXiv、GitHub；使用 AI 时还需能访问配置的模型服务。
3. 打开 **Actions → arXiv manual announcement backfill → Run workflow**。
4. 填写 `start_date` 和 `end_date`，格式为 `YYYY-MM-DD`，包含起止两天。
5. `categories` 留空时使用仓库变量 `CATEGORIES`；也可以填写 `cond-mat.quant-gas,quant-ph` 等分类。
6. 默认开启 `enhance`，沿用仓库的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` Secrets 和 `MODEL_NAME`、`LANGUAGE` Variables。
   `LANGUAGE` 未设置时使用 Chinese。关闭 `enhance` 只保存原始论文数据，不调用模型；网站的现有列表主要读取 AI 增强文件。
7. 默认 `overwrite=false`，保留已有日期的数据，补做缺失的 AI 摘要或 Markdown。更改分类后要重新抓取同一日期，应开启 `overwrite`。

例如，补抓 2026-11-09 至 2026-11-12，将这两个日期分别填入起止输入框。示例只在它们仍处于最近 90 天内时有效。
单日补抓时填写两个相同日期。

## 日期的准确含义和限制

- 直接读取 `https://arxiv.org/catchup/<category>/<YYYY-MM-DD>?abs=True&page=1`。
- 使用 **Catchup 页面标注的 announcement date**，不根据 submission date、API `published` 或提交截止时间推算。
- arXiv 官方 Catchup 服务只开放最近约 90 天。本脚本保守校验为当前 UTC 日期向前 90 天至今天；更早或未来日期会直接报错。
- 收集 **New submissions** 和 **Cross submissions**，排除 **Replacement submissions**。同一天跨分类出现的论文按 arXiv ID 去重；不跨公告日删除论文。
- 结果包含 `announcement_date` 和 `announcement_type`。类别顺序、标题、作者、摘要来自 Catchup 页面。
- Catchup 提供的是历史公告成员列表，但展示的论文元数据可能是当前修订版，不承诺重建公告当天的标题或摘要快照。
- 没有公告或该分类没有新论文的日期标记为 `empty`，不创建空数据文件。当天公告尚未发布时也可能为空，稍后可以重跑。
- 服务端拒绝日期、HTTP 错误、页面日期不匹配、条目缺失或结构改变都会使任务失败，不会当作“当天没有论文”。429/5xx 有限重试；请求间隔至少 3 秒。

参考 arXiv 自身实现：
[日期校验与分页](https://github.com/arXiv/arxiv-browse/blob/develop/browse/controllers/catchup_page.py)、
[公告成员查询与元数据](https://github.com/arXiv/arxiv-browse/blob/develop/browse/services/database/catchup.py)。

## 保存、重跑和恢复

- 结果保存到 `data` 分支的 `data/YYYY-MM-DD.jsonl`；开启 AI 后生成对应的 `_AI_enhanced_<LANGUAGE>.jsonl` 和 `.md`。
- `data` 分支不存在时自动建立；已有数据会先完整检出，再更新 `assets/file-list.txt`，保留其他日期的历史记录。
- 沿用现有 AI 增强程序及其内容筛选规则，因此 AI 文件可能比原始文件少一些论文。
- 默认保留已有原始文件，不重新判定其原始日期语义。若要用准确的 Catchup 公告数据替换旧版每日任务生成的同名文件，请选 `overwrite=true`。
- 覆盖模式先在临时目录完成当天的抓取、摘要和转换，失败不会覆盖当天原有文件。成功后清理该日期其他语言的过期摘要；关闭 AI 的覆盖模式会移除旧摘要和 Markdown，避免展示不匹配的数据。
- 整个范围成功后才提交、推送数据。失败时不推送部分结果；成功完成的日期会随运行报告保存为 GitHub Actions artifact，保留 7 天。
- 正常重跑使用已发布的数据跳过完成日期；若上次在发布前失败，可下载 artifact 取回已完成结果，否则重跑可能再次调用 AI。
- 手动 workflow 之间串行执行。如果另一个任务同时更新 `data` 分支，推送会安全失败，不会强制覆盖远端；重新运行即可读取最新数据再处理。
- 本功能不修改网站认证或配置。已有站点继续读取 `data` 分支；新部署的站点仍需要按 README 配置 GitHub Pages 和仓库信息。

## 本地运行和测试

```bash
uv sync --locked
export CATEGORIES='cond-mat.quant-gas,quant-ph'
uv run python scripts/backfill_arxiv.py \
  --start-date 2026-11-09 --end-date 2026-11-12 --store ./archive
# 加 --enhance 开启摘要；加 --overwrite 重新抓取已有日期。
uv run python -m unittest discover -s scripts/tests -v
```

测试使用根据 arXiv 官方模板构造的 HTML，以及模拟网络/模型响应，不访问模型服务，也不消耗 API 额度。
