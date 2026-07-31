# 评测样本

每个样本一对文件,**同名不同后缀**:

- 输入:一张图(`.png` / `.jpg` / `.jpeg` / `.webp` / `.gif`)**或**一个 `.txt`
- 期望:同名 `.yaml`

`.yaml` 字段:

```yaml
expected_domain_group: 量子力学          # 一致性检验的分组标签(必填)
expected_title_keywords: [角动量, 对易]   # 标题里应出现的关键词
expected_note_count: [1, 3]             # 合理条数区间 [下限, 上限]
expected_category: physics/quantum-mechanics   # 可选,taxonomy 非空时才用得上
```

比如 `角动量.png` + `角动量.yaml`,或 `缓存失效.txt` + `缓存失效.yaml`。

样本按**文件名排序**依次处理,一致性指标会模拟冷启动累积:每处理完一个样本,
就把它产出的 `suggested_domain` 加进已知领域,再处理下一个。所以文件名顺序会影响
收敛过程,可用数字前缀控制顺序(如 `01_角动量.png`)。

跑:

```
uv run python -m evals.run [--extract-model X] [--classify-model Y] [--limit N]
```
