# PromQL Patterns for Grafana — Production Reference

Real patterns from building dashboards against live Prometheus exporters.
Every common mistake is documented because they all happened.

---

## The Most Important Rule

**NEVER join inside a range vector function.**

```promql
# ❌ INVALID — binary op on range vector
rate(builds_total * on(projectID) group_left(projectName) project_info [5m])
# Error: "bad_data: binary expression must contain only scalar and instant vector types"

# ✅ VALID — rate first, then join instant vectors
rate(builds_total[5m]) * on(projectID) group_left(projectName) project_info
```

Same rule applies to `increase()`, `delta()`, `irate()`, `avg_over_time()`, etc.

---

## Metric Discovery Before Building

Always inspect a new exporter before writing queries.

```bash
# 1. List all metric names (filter by exporter prefix):
curl -s "http://prometheus:9090/api/v1/label/__name__/values" | \
  python3 -c "import json,sys; [print(m) for m in json.load(sys.stdin)['data'] if 'myexporter' in m]"

# 2. What labels does a metric ACTUALLY have?
curl -s "http://prometheus:9090/api/v1/query?query=my_metric" | \
  python3 -c "
import json,sys
r = json.load(sys.stdin)['data']['result']
if r:
    print('Labels:', list(r[0]['metric'].keys()))
    print('Example:', json.dumps(r[0]['metric'], indent=2))
    print('Series count:', len(r))
else:
    print('NO DATA — metric does not exist or is empty')
"

# 3. What are all values of a specific label?
curl -s "http://prometheus:9090/api/v1/label/projectName/values" | \
  python3 -c "import json,sys; [print(v) for v in json.load(sys.stdin)['data']]"

# 4. Does a summary metric have quantile labels?
curl -s "http://prometheus:9090/api/v1/query?query=my_summary" | \
  python3 -c "
import json,sys
r = json.load(sys.stdin)['data']['result']
print('quantile values:', sorted({x['metric'].get('quantile','none') for x in r}))
print('total series:', len(r))
"
# If output is {'none'} — no quantiles; use sum/count ratio instead
```

---

## Label Join Patterns

### Add one name label to a metric

Metric has `projectID` but you need `projectName` for the legend/filter:

```promql
metric
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
```

### Add two name labels in one expression

```promql
metric
  * on(buildDefinitionID, projectID) group_left(buildDefinitionName)
    build_definition_info{buildDefinitionName=~"$buildDefinition"}
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
```

### Join via intermediate metric (two hops)

The metric `agent_job` has `agentPoolAgentID` but no `agentPoolName`.
Get pool name via `agent_info`:

```promql
agent_job{planType!="PoolMaintenance"}
  * on(agentPoolAgentID) group_left(agentPoolID, agentPoolName)
    agent_info{agentPoolName=~"$agentPool"}
```

### Join for legend only (no filter on the info metric)

Omit the `{}` filter to join all series regardless of label value:

```promql
metric
  * on(projectID) group_left(projectName)
    project_info
```

### label_replace to rename a label before joining

When a metric uses `scopeID` for what is logically `projectID`:

```promql
label_replace(
  agent_job{planType!="PoolMaintenance"},
  "projectID", "$1", "scopeID", "^(.+)$"
)
* on(projectID) group_left(projectName) project_info{projectName=~"$project"}
```

---

## Rate / Increase Patterns

### Per-second rate, then join

```promql
sum by(projectName)(
  rate(builds_total[5m])
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
)
```

### Sum of increases (24h totals)

```promql
sum by(result)(
  increase(builds_total{result=~"$buildResult"}[24h])
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
)
```

### Average (using _sum / _count metrics)

For Prometheus summaries that export `_sum` and `_count` but no quantiles:

```promql
# Simple average:
sum(duration_sum) / sum(duration_count)

# Average per group, then join:
(
  sum by(buildDefinitionID, projectID)(duration_sum)
  / sum by(buildDefinitionID, projectID)(duration_count)
)
* on(buildDefinitionID, projectID) group_left(buildDefinitionName) build_def_info
* on(projectID) group_left(projectName) project_info{projectName=~"$project"}

# Moving average using rate:
sum(increase(duration_sum[10m])) /
sum(increase(duration_count[10m]))
```

---

## Count / Presence Patterns

### Count matching series

```promql
# How many open PRs?
count(pullrequest_info
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"})

# Count with label filter:
count(pullrequest_info{voteStatus="Approved"}
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"})

# Count grouped by a label:
count by(voteStatus)(
  pullrequest_info
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"})
```

### Time-based filter (age threshold)

```promql
# PRs older than 7 days (604800 seconds):
count(
  (time() - pullrequest_status{type="created"}
   * on(projectID) group_left(projectName)
     project_info{projectName=~"$project"}
  ) >= 604800
)

# All PR ages as individual values (for histogram):
time() - pullrequest_status{type="created"}
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}

# Average age:
avg(
  time() - pullrequest_status{type="created"}
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
)
```

---

## topk Patterns

```promql
# Top 10 slowest pipelines:
topk(10,
  avg by(buildDefinitionID, projectID)(
    builds_duration_sum / builds_duration_count
  )
  * on(buildDefinitionID, projectID) group_left(buildDefinitionName)
    build_definition_info
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"}
)

# Top 10 by count with join:
topk(10,
  sum by(agentPoolID)(
    increase(agentpool_builds[24h])
  )
  * on(agentPoolID) group_left(agentPoolName)
    agentpool_info{agentPoolName=~"$agentPool"}
)
```

---

## DORA Metric Queries

```promql
# Deployment Frequency (successful builds per day):
round(
  sum(rate(builds{result="succeeded"}[1h])
    * on(projectID) group_left(projectName)
      project_info{projectName=~"$project"}
  ) * 86400, 1
)

# Change Failure Rate (%):
sum(rate(builds{result="failed"}[1h])
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"})
/ sum(rate(builds[1h])
  * on(projectID) group_left(projectName)
    project_info{projectName=~"$project"})
* 100

# CI/CD Health Score (success rate %):
avg(success_sum * on(projectID) group_left(projectName) project_info{projectName=~"$project"})
/ avg(success_count * on(projectID) group_left(projectName) project_info{projectName=~"$project"})
* 100
```

---

## Variable Queries

```promql
# Simple list of all values for a label:
label_values(project_info, projectName)

# Dependent/cascading variable (filter on parent):
label_values(build_definition_info{projectName=~"$project"}, buildDefinitionName)

# Filtered by another label:
label_values(agent_info{agentPoolName=~"$agentPool"}, agentName)
```

**Using variables in queries:**
```promql
# Single-select:   {label="$variable"}
# Multi-select:    {label=~"$variable"}   ← always use =~ for multi
# With allValue=".*" this matches everything when All is selected
```

---

## Common Gotchas (All From Real Sessions)

### Gotcha 1: Stats metrics have only IDs, not names

Counters like `builds_total` often only carry `projectID`, not `projectName`.
The name only exists on info/metadata metrics.

```promql
# ❌ Will return no data — projectName not on this metric:
builds_total{projectName=~"$project"}

# ✅ Join info metric to get the name:
builds_total * on(projectID) group_left(projectName) project_info{projectName=~"$project"}
```

### Gotcha 2: Wrong label names

Different exporters use different label names for the same concept.
Always inspect before assuming:

```
pullrequestCreatedBy  vs  creator
pullrequestID         vs  pullrequest_id  (capitals matter!)
buildDefinitionName   vs  definitionName  vs  buildName
```

### Gotcha 3: Summary metrics without quantiles

If `my_summary_sum` and `my_summary_count` exist but the base metric
`my_summary` returns no `quantile` label, you CANNOT use `histogram_quantile()`.
Use `_sum / _count` ratio instead. Remove all P50/P90/P99 panels.

### Gotcha 4: Release/deployment metrics have no name labels

Many exporters expose release metrics with only IDs:
```
release_info{projectID="...", releaseDefinitionID="..."}  ← no names
```
You must join `release_definition_info` for `releaseDefinitionName`
and `project_info` for `projectName`.

### Gotcha 5: Agent job metrics have no pool labels

`agent_job` often has `agentPoolAgentID` and `scopeID` (= projectID) but
no `agentPoolName`. Join through `agent_info` to get pool context.

### Gotcha 6: allValue must be `".*"` for multi-select

When a user selects "All" in a Grafana multi-select variable, Grafana
sends the `allValue` string as the label matcher value.
- `allValue: ""` → sends empty string → no matches
- `allValue: ".*"` → sends regex that matches everything → works

### Gotcha 7: `definition` field in variable must match `query.query`

```python
# ❌ Breaks on dashboard reload:
{"query": {"query": "label_values(project_info, projectName)"}, "definition": ""}

# ✅ Must be identical:
q = "label_values(project_info, projectName)"
{"query": {"query": q}, "definition": q}
```

### Gotcha 8: Emojis in Python f-strings

Emojis inside f-strings containing Prometheus `{{label}}` patterns cause
Python `SyntaxError: invalid character`.

```python
# ❌ SyntaxError:
expr = f'count(metric{{voteStatus="Approved"}} * on(id) info{{name=~"$p"}})'
legend = "✅ Approved"  # fine
# But if you try to put ✅ INSIDE the f-string expr, it fails.

# ✅ Keep emoji in legendFormat string, not in the expr f-string:
tgt(
    f'count(metric{{voteStatus="Approved"}} * on(id) info{{name=~"{P}"}})',
    "✅ Approved",   # emoji here is safe — it's a plain string
    "A"
)
```

### Gotcha 9: Vector matching on inconsistent metric names

```
parse error: vector matching must be on consistent metric name
```

This occurs when both sides of `*` have different `__name__` labels
AND you haven't used `ignoring(__name__)`. Usually means you chained
two non-info metrics together instead of one metric + one info metric.

### Gotcha 10: `increase()` with joins returns non-integer values

`increase()` extrapolates to the full window. When combined with joins,
series alignment can cause decimal values. Use `round()` for display:

```promql
round(
  increase(builds_total[24h])
  * on(projectID) group_left(projectName) project_info,
  1
)
```

---

## Quick Reference Table

| Task | Pattern |
|------|---------|
| Add name label | `metric * on(id) group_left(name) info` |
| Add 2 name labels | Chain two `* on(...)` joins |
| Rate then join | `rate(metric[5m]) * on(id) group_left(name) info` |
| Increase then join | `increase(metric[24h]) * on(id) group_left(name) info` |
| Average (summary) | `sum(duration_sum) / sum(duration_count)` |
| Count with filter | `count(metric{label="val"} * on(id) group_left(name) info)` |
| Age in seconds | `time() - timestamp_metric` |
| Top N | `topk(N, avg by(labels)(metric) * on(...) info)` |
| Rename label for join | `label_replace(metric, "new", "$1", "old", "^(.+)$")` |
| Per-project breakdown | `sum by(projectName)(rate(metric[5m]) * on(projectID) group_left(projectName) info)` |
