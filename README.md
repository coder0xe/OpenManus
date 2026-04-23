# OpenManus 工具调用链插桩说明

本说明文档对应当前这版 **OpenManus 插桩补丁** 的真实改动范围，重点是帮助你理解：

- 当前到底改了哪些文件
- 这些改动能支撑哪些 workflow 分析
- 目前还缺哪些链路
- 如何把补丁覆盖到现有仓库中

> **注意**
>
> 当前版本属于 **工具调用主链插桩**，还不是“完整 agent 内部全链路插桩”。
>
> 因此，它适合用于分析 **OpenManus 的工具主导型 workflow**，不建议直接表述为“完整 agent workflow tracing”。

---

## 一、当前真实改动范围

当前补丁中，**确认存在实际插桩改动** 的文件如下。

### 1. Agent 主链

- `app/agent/base.py`
- `app/agent/toolcall.py`

### 2. Tool 主链

- `app/tool/base.py`
- `app/tool/tool_collection.py`

### 3. 细粒度工具

- `app/tool/bash.py`
- `app/tool/browser_use_tool.py`

### 4. 新增观测模块

- `app/observability/__init__.py`
- `app/observability/tool_context.py`
- `app/observability/tracing.py`

---

## 二、当前没有完成插桩的部分

以下内容 **不在当前真实完成范围内**：

- `app/agent/react.py`
- `app/agent/manus.py`
- `app/llm.py`
- 启动入口文件中的 Phoenix / OpenTelemetry 注册逻辑
  - 例如：`main.py`、`run_flow.py`、`run_mcp.py` 或你实际使用的启动脚本

这意味着当前版本 **没有完成**：

- `ReActAgent.step / think / act` 的独立 span
- `Manus` 初始化过程的独立 span
- MCP 动态工具接入过程的独立 span
- LLM 请求级 span
- 完整 state / memory 演化链路
- Phoenix / OTel exporter 的入口注册

---

## 三、当前版本能分析什么

当前补丁主要覆盖下面这条链路：

```text
agent.run / step
  -> plan_tools
  -> tool_call
  -> tool.dispatch
  -> tool.execute
```

基于这条链路，当前版本可以支撑以下分析。

### 1. Step 级执行流程分析

可以观察：

- agent 一共执行了多少轮 step
- 每一轮 step 是否进入工具规划阶段
- 哪些 step 真正触发了工具调用
- 工具调用在 step 之间的分布情况

### 2. 工具调用主链分析

可以观察：

- 规划出了哪些工具调用
- 工具名称、参数和结果概览
- 工具分发是否成功
- 实际由哪个工具类完成执行
- 调用耗时、异常与成功率

### 3. 关键工具行为分析

当前已经对两类常见高价值工具做了更细的字段增强：

#### Bash

可用于分析：

- agent 是否频繁依赖 shell
- 命令执行是否失败
- 输出是否异常膨胀
- shell 在 workflow 中承担什么角色

#### BrowserUseTool

可用于分析：

- 浏览器工具内部执行了什么动作
- 是搜索、跳转、点击还是输入
- 浏览器操作在任务推进中的作用

---

## 四、当前版本不能直接支持什么分析

当前版本 **不适合直接声称支持** 以下分析：

### 1. 完整 agent 内部 workflow 分析

因为尚未拆出：

- `ReActAgent.step`
- `ReActAgent.think`
- `ReActAgent.act`

所以当前还不能精确区分：

- 一轮 step 中思考和执行各花了多少时间
- think 阶段是否出现反复犹豫
- act 阶段是否执行了多个动作
- step 为什么在某个时刻结束

### 2. 完整 LLM 调用分析

因为 `app/llm.py` 尚未补齐请求级 span，当前不能直接分析：

- 每轮 think 发起了多少次 LLM 请求
- prompt / response 延迟
- tool schema 大小与调用延迟的关系
- 哪次 LLM 调用触发了具体 tool call

### 3. 完整初始化与动态工具装载分析

因为 `manus.py` 和入口层尚未补齐，当前不能完整分析：

- 默认工具如何装配
- MCP server 如何初始化
- MCP 工具在什么时候注入 agent
- 不同运行模式下工具环境如何形成

### 4. 完整状态与记忆演化分析

当前没有独立插出：

- memory append
- state transition
- terminate / finish 原因

因此很难直接回答：

- agent 为什么在第 N 步结束
- 哪一步写入了关键信息并改变后续策略
- memory 增长是否导致后续 step 变慢

---

## 五、当前插桩设计

当前补丁采用的是 **主链优先、局部细化** 的设计。

### 1. Agent 逻辑层

在 `app/agent/toolcall.py` 中插入逻辑层 span，用于记录：

- 当前 step
- 当前工具调用名称
- 工具参数
- 工具调用结果概览
- 调用异常信息

该层负责描述：

> 模型决定调用什么工具，以及工具调用逻辑何时开始。

### 2. Tool 执行层

在 `app/tool/base.py` 中增加统一 `tool.execute` 插桩。

所有通过 `BaseTool` 执行的工具，都会经过这一层。

该层负责描述：

- 具体执行的是哪个工具类
- 传入参数是什么
- 执行耗时是多少
- 是否成功
- 是否抛出异常

### 3. Tool 分发层

在 `app/tool/tool_collection.py` 中增加 dispatch 级 span。

该层负责描述：

- 工具名如何映射到工具对象
- 分发是否成功
- 分发失败原因

### 4. 工具细化层

在 `bash.py` 和 `browser_use_tool.py` 中补充工具特有字段，便于进行动作级分析。

---

## 六、各文件作用说明

### `app/agent/base.py`

负责建立最外层的 step 骨架，当前用于记录：

- `agent.run`
- `agent.step`

这是整个工具调用链的最外层观察框架。

---

### `app/agent/toolcall.py`

这是当前版本中最关键的文件之一。

主要负责：

- 工具规划阶段的可观测性
- 工具执行逻辑阶段的可观测性

它把“模型决定调用工具”和“工具真正被执行”连接起来。

---

### `app/tool/base.py`

这是当前版本中最关键的另一个文件。

它为所有 `BaseTool` 子类提供统一执行入口插桩，是最稳定的工具执行观测点。

---

### `app/tool/tool_collection.py`

负责补充工具调度与分发这一层，便于观察：

- 工具名称到工具实例的映射
- dispatch 成功与否
- dispatch 侧异常

---

### `app/tool/bash.py`

负责增强 Bash 工具的观测字段，例如：

- command
- 输出长度
- 错误信息
- 执行耗时

适合分析 shell 在 agent workflow 中的角色。

---

### `app/tool/browser_use_tool.py`

负责增强浏览器工具的动作级字段，例如：

- action
- URL
- query
- index
- 结果概览

适合分析浏览器工具在任务推进中的具体作用。

---

### `app/observability/tool_context.py`

通过上下文变量传递运行期观测上下文，例如：

- 当前 step
- 当前 tool call
- agent 运行时的局部上下文

用于把不同层的 span 串联起来。

---

### `app/observability/tracing.py`

用于统一封装 tracing 能力，目标是：

- 减少业务代码与 tracing 细节的耦合
- 在未配置 OTel 时安全退化
- 为后续接 Phoenix / OpenTelemetry 做准备

---

## 七、如何把补丁覆盖到仓库中

当前建议只覆盖 **真实有改动** 的文件，不要按更大范围误覆盖。

### 需要覆盖 / 新增的文件

```text
app/agent/base.py
app/agent/toolcall.py
app/tool/base.py
app/tool/tool_collection.py
app/tool/bash.py
app/tool/browser_use_tool.py
app/observability/__init__.py
app/observability/tool_context.py
app/observability/tracing.py
```

### 示例命令

在 OpenManus 仓库根目录执行：

```bash
cp /path/to/openmanus_instrumented/app/agent/base.py app/agent/base.py
cp /path/to/openmanus_instrumented/app/agent/toolcall.py app/agent/toolcall.py

cp /path/to/openmanus_instrumented/app/tool/base.py app/tool/base.py
cp /path/to/openmanus_instrumented/app/tool/tool_collection.py app/tool/tool_collection.py
cp /path/to/openmanus_instrumented/app/tool/bash.py app/tool/bash.py
cp /path/to/openmanus_instrumented/app/tool/browser_use_tool.py app/tool/browser_use_tool.py

mkdir -p app/observability
cp /path/to/openmanus_instrumented/app/observability/__init__.py app/observability/__init__.py
cp /path/to/openmanus_instrumented/app/observability/tool_context.py app/observability/tool_context.py
cp /path/to/openmanus_instrumented/app/observability/tracing.py app/observability/tracing.py
```

---

## 八、当前版本与 Phoenix / OTel 的关系

当前补丁的本质是：

> **把埋点代码写进 OpenManus 的核心调用链中。**

但这还不等于你一定能在 Phoenix 中看到完整 trace。

原因是：

- 当前版本主要完成了 **内部 span 埋点**
- 还没有完成 **启动入口中的 OTel / Phoenix exporter 注册**

如果入口没有注册 exporter，则可能出现：

- span 退化为 no-op
- 代码里看起来有埋点，但 Phoenix 里没有 trace
- trace 树不完整

因此，如果你要真正接 Phoenix，还需要继续补：

- `main.py`
- `run_flow.py`
- `run_mcp.py`
- 或你实际使用的启动脚本

使程序在启动时完成：

- tracer provider 初始化
- OTLP exporter 注册
- service name 配置
- endpoint 配置

---

## 九、建议如何描述当前版本

### 建议表述

建议把当前版本描述为：

> **OpenManus 工具调用主链插桩**
> 对 step、tool planning、tool dispatch 和 tool execute 过程进行了可观测性增强，
> 可用于分析 OpenManus 的工具主导型执行 workflow。

### 不建议表述

不建议直接表述为：

> **OpenManus 完整 agent workflow tracing**

因为当前尚未覆盖完整 agent 内部全链路。

---

## 十、下一步建议

如果要把当前版本扩展为“完整 agent 内部全链路插桩”，建议按以下顺序继续补：

### 第一优先级

1. `app/agent/react.py`
   - `step`
   - `think`
   - `act`

2. `app/llm.py`
   - 请求级 span
   - latency / message size / token 信息

### 第二优先级

1. `app/agent/manus.py`
   - 默认工具装配
   - MCP 初始化
   - 动态工具注入

2. state / memory 相关链路
   - memory append
   - terminate / finish 原因
   - 状态切换

### 第三优先级

1. 启动入口
   - Phoenix / OpenTelemetry 注册
   - exporter 配置
   - service name 配置

---

## 十一、一句话总结

当前补丁已经能够支持你分析 **OpenManus 的工具调用主链 workflow**，尤其适合做：

- step 级粗粒度流程分析
- 工具调用序列分析
- Bash / Browser 等关键工具行为分析

但它还不足以支撑 **完整 agent 内部 workflow tracing**。
