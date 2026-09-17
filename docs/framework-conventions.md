# Framework Conventions

## 1. Error Code

允许：

```python
raise AppError(ErrorCode.AGENT_NOT_FOUND)
```

带动态参数时：

```python
raise AppError('RESOURCE_NOT_FOUND', message_args={'resource': 'Agent'})
```

禁止：

```python
raise HTTPException(status_code=404, detail='Agent 不存在')
return {'code': 'X', 'msg': 'Agent 不存在'}
```

新的错误码、中英文和 HTTP status 只增加 `config/api-messages.yaml`。

## 2. Frontend i18n

页面只使用 `t(key)`；新增业务补：

```text
zh-CN.json
en-US.json
```

框架已处理语言切换、localStorage、X-Locale、后端错误 Toast。

## 3. Logging

服务入口只调用：

```python
configure_logging(SERVICE_NAME)
```

部署只配置：

```text
LOG_DIR
```

输出：

```text
LOG_DIR/service/YYYY-MM-DD.log
```

## 4. Artifact

```text
NFS/PVC = immutable Artifact source
emptyDir = runtime local cache
PostgreSQL = metadata + storage_key
RuntimeSnapshot = artifact_id + checksum + storage_key
```
