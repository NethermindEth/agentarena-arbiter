# 安全漏洞评估系统 API

这个API系统用于管理和评估安全漏洞，提供自动化的漏洞去重、相似性检测和AI辅助的安全评估功能。

## 安装指南

1. 安装依赖项：

```bash
pip install -r requirements.txt
```

2. 配置环境变量：

复制 `.env.example` 到 `.env` 并填入必要的配置信息：

```
# MongoDB连接设置
MONGO_URI=mongodb://localhost:27017

# Claude API密钥
CLAUDE_API_KEY=your_api_key_here

# 漏洞相似度阈值 (0.0-1.0)
SIMILARITY_THRESHOLD=0.8
```

## 运行API服务

启动FastAPI服务：

```bash
# 开发模式
uvicorn app.main:app --reload

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

服务将在 http://localhost:8000 上运行。API文档可在 http://localhost:8000/docs 访问。

## API端点说明

### 漏洞处理与自动评估

- **POST /process_findings**
  - 提交漏洞进行处理（可以是单个或多个）
  - 执行漏洞去重处理
  - 自动评估新提交的漏洞（无需额外API调用）
  - 返回去重结果和评估结果的组合

### 查询端点

- **GET /tasks/{task_id}/findings**
  - 获取任务的所有漏洞

## 漏洞处理流程（/process_findings）

`/process_findings`端点实现了一个完整的端到端漏洞处理流程，包括去重、存储和智能评估。以下是详细的处理步骤：

### 1. 漏洞接收与验证
- 接收`FindingInput`对象，包含`task_id`、`agent_id`和`findings`列表
- 验证输入数据的完整性和格式

### 2. 漏洞去重处理
- 通过`FindingDeduplication.process_findings()`执行漏洞去重
- 获取该代理在同一任务中已提交的有效漏洞（排除已报告的重复）
- 对每个新提交的漏洞计算与现有漏洞的相似度
- 基于相似度阈值（SIMILARITY_THRESHOLD环境变量）判断重复性
- 对重复漏洞标记为`Status.ALREADY_REPORTED`
- 对相似漏洞标记为`Status.SIMILAR_VALID`
- 对新漏洞保持`Status.PENDING`，等待评估

### 3. 存储到数据库
- 为每个处理后的漏洞创建`FindingDB`对象
- 批量保存到MongoDB数据库（集合名为`findings_{task_id}`）
- 记录创建和更新时间戳

### 4. 自动评估（仅针对新漏洞）
- 检查是否有新漏洞被添加（dedup_results['new'] > 0）
- 获取所有处于PENDING状态的漏洞
- 对每个PENDING漏洞执行AI评估：
  1. 构建评估查询，包含漏洞标题、描述和严重性
  2. 调用Claude AI模型评估漏洞
  3. 解析AI响应，提取评估结果：
     - 有效性（is_valid）
     - 安全类别（category）
     - 评估后的严重性（evaluated_severity）
     - 评估意见（evaluation_comment）
  4. 根据评估结果更新漏洞状态：
     - 有效漏洞：更新为`Status.UNIQUE_VALID`
     - 无效漏洞：更新为`Status.DISPUTED`
  5. 保存评估结果到数据库

### 5. 生成响应
- 组合去重结果和评估结果
- 返回完整的处理报告，包括：
  - 去重统计：总数、新增数、重复数
  - 评估结果：评估为有效的数量、有争议的数量
  - 每个漏洞的详细评估信息
  - 任务的总体摘要报告

### 6. 错误处理
- 捕获整个处理过程中的异常
- 返回适当的HTTP状态码和错误详情

该流程实现了漏洞从提交到最终评估的全自动处理，无需多个API调用，大大简化了客户端集成和使用。

## 运行测试

### 基本功能测试

测试基本漏洞处理功能：

```bash
python -m app.test.test_findings
```

### 批量处理测试

测试大量漏洞的批量处理：

```bash
python -m app.test.test_batch_processing
```

### API集成测试

测试process_findings API端点的端到端功能：

```bash
# 先启动API服务
uvicorn app.main:app --reload

# 然后在新终端中运行
python -m app.test.test_process_findings
```

### 运行所有测试

一次性运行所有测试：

```bash
python -m app.test.run_tests
```

## 数据模型

### FindingInput (输入模型)

```python
class FindingInput:
    task_id: str            # 任务ID
    agent_id: str           # 代理ID
    findings: List[Finding] # 漏洞列表
```

### FindingDB (数据库模型)

```python
class FindingDB:
    # 基本信息
    title: str
    description: str
    severity: Severity      # 原始严重性 (HIGH/MEDIUM)
    agent_id: str           # 提交代理
    
    # 系统评估信息
    status: Status          # 状态 (PENDING/ALREADY_REPORTED/SIMILAR_VALID/UNIQUE_VALID/DISPUTED)
    category: Optional[str] # 安全类别
    category_id: Optional[str] # 类别组ID
    evaluated_severity: Optional[EvaluatedSeverity] # 评估严重性 (HIGH/MEDIUM/LOW)
    evaluation_comment: Optional[str] # 评估意见
    
    # 时间戳
    created_at: datetime
    updated_at: datetime
```

## 错误处理

API会返回适当的HTTP状态码和详细的错误信息：

- 400: 请求无效
- 404: 资源不存在
- 500: 服务器内部错误

每个错误响应都包含一个`detail`字段，提供具体的错误信息。 