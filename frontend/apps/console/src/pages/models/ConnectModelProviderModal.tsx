import { useEffect, useState } from "react";

import { Button, Checkbox, Input, Modal, Select, Space, Toast, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, JsonRecord, ResourceVersion } from "../../types/console";
import { CreateCredentialModal } from "../secrets/CreateCredentialModal";

interface ConnectModelProviderModalProps {
  readonly api: ConsoleApi;
  readonly visible: boolean;
  readonly onClose: () => void;
  readonly onConnected: () => void;
  /** 刷新模式：对既有 Provider 重新 Test Connection / Discover（不重复创建）。 */
  readonly provider?: {
    readonly baseUrl: string;
    readonly credentialRef: string;
    readonly displayName: string;
    readonly resourceId: string;
  } | null;
}

interface CredentialOption {
  readonly label: string;
  readonly value: string;
}

/** TASK-010：ConnectModelProviderModal——连接模型服务完整 Journey（§8.7）。
 *
 * Provider 类型/名称/Endpoint/Credential Select（数据源凭据列表，内嵌
 * 「+ 新增凭据」）→ 测试连接（真实探测 base_url/models）→ Discover Models
 * 勾选添加；Provider 不支持发现时「+ 手工添加模型」。Credential 禁止 raw
 * credential_ref 输入（§10）。完成 = 发布 Provider + 创建并发布所选模型。
 */
export function ConnectModelProviderModal({
  api,
  visible,
  onClose,
  onConnected,
  provider = null
}: ConnectModelProviderModalProps) {
  const refreshMode = provider !== null;
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [credentialOptions, setCredentialOptions] = useState<readonly CredentialOption[]>([]);
  const [credentialModalVisible, setCredentialModalVisible] = useState(false);
  const [createdProvider, setCreatedProvider] = useState<ResourceVersion | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [reachable, setReachable] = useState(false);
  const [discovered, setDiscovered] = useState<readonly string[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [manualModels, setManualModels] = useState<string[]>([]);
  const [manualInput, setManualInput] = useState("");
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    setName(provider?.displayName ?? "");
    setEndpoint(provider?.baseUrl ?? "");
    setCredentialRef(provider?.credentialRef ?? "");
    setTesting(false);
    setTestError(null);
    setReachable(false);
    setDiscovered([]);
    setSelected(new Set());
    setManualModels([]);
    setManualInput("");
    setError(null);
    if (provider === null) {
      setCreatedProvider(null);
      return () => {
        active = false;
      };
    }
    // S-05：刷新模式必须读取既有 Provider 真实版本，禁止伪造 version="1"/draft——
    // 伪造会导致 finish() 重复发布已发布版本（后端 VersionConflictError → 409），
    // 且新建模型会 pin 错误的 provider 版本。
    setCreatedProvider(null);
    void (async () => {
      try {
        const real = await api.getResource("model_provider", provider.resourceId);
        if (active) setCreatedProvider(real);
      } catch {
        if (active) setError("无法读取既有模型服务版本，刷新不可用");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, visible, provider]);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void (async () => {
      try {
        const pageData = await api.listResources("secret");
        const details = await Promise.all(
          pageData.items.map((item) => api.getResource("secret", item.resourceId))
        );
        if (!active) return;
        setCredentialOptions(
          pageData.items.map((item, index) => ({
            label: String((details[index].spec as JsonRecord).name ?? item.resourceId),
            value: String((details[index].spec as JsonRecord).secret_ref ?? "")
          }))
        );
      } catch {
        // 凭据列表加载失败时 Select 呈现空选项，内嵌新增入口仍可用
        if (active) setCredentialOptions([]);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, visible, credentialModalVisible]);

  const hasSelection = selected.size > 0 || manualModels.length > 0;

  async function testConnection(): Promise<void> {
    if (!endpoint.trim()) {
      setError("Endpoint：必填");
      return;
    }
    if (!credentialRef) {
      setError("凭据：必选（Credential 禁止手工填写 raw ref）");
      return;
    }
    if (!refreshMode && !name.trim()) {
      setError("模型服务名称：必填");
      return;
    }
    setTesting(true);
    setTestError(null);
    setError(null);
    try {
      let target = createdProvider;
      if (target === null) {
        if (refreshMode) {
          // 真实版本尚未就绪时禁止新建 Provider——否则会产生重复 Provider。
          setError("既有模型服务版本尚未就绪，请稍候再试");
          return;
        }
        target = await api.createModelProvider({
          display_name: name.trim(),
          protocol: "openai-compatible",
          base_url: endpoint.trim(),
          credential_ref: credentialRef,
          request_timeout_ms: 60_000,
          max_retries: 1
        });
        setCreatedProvider(target);
      }
      const result = await api.testModelProviderConnection(target.resourceId);
      setReachable(result.reachable);
      setDiscovered(result.discoveredModels);
      if (!result.reachable) {
        setTestError(result.error ?? "连接失败：凭据或端点错误");
      } else {
        try {
          Toast.success("连接成功");
        } catch {
          // jsdom/无 Toast 容器下渲染失败不阻断
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "测试连接失败");
    } finally {
      setTesting(false);
    }
  }

  function toggleModel(model: string, checked: boolean): void {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(model);
      else next.delete(model);
      return next;
    });
  }

  function addManualModel(): void {
    const trimmed = manualInput.trim();
    if (!trimmed || manualModels.includes(trimmed)) return;
    setManualModels((previous) => [...previous, trimmed]);
    setManualInput("");
  }

  async function finish(): Promise<void> {
    if (createdProvider === null) {
      setError("请先测试连接");
      return;
    }
    if (!hasSelection) {
      setError("请至少选择或添加一个模型");
      return;
    }
    setFinishing(true);
    setError(null);
    try {
      // 发布 Provider（Golden Path 解析 latest-published；凭据可解析性由发布校验把关）
      const published =
        createdProvider.status === "draft"
          ? await api.publishVersion(createdProvider)
          : null;
      const providerVersion = published?.version ?? createdProvider.version;
      const models = [...selected, ...manualModels];
      for (const model of models) {
        const definition = await api.createModelDefinition({
          name: model,
          provider_ref: { id: createdProvider.resourceId, version: providerVersion }
        });
        await api.publishVersion(definition);
      }
      onConnected();
      try {
        Toast.success("模型服务连接完成");
      } catch {
        // jsdom/无 Toast 容器下渲染失败不阻断
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "完成连接失败");
    } finally {
      setFinishing(false);
    }
  }

  return (
    <Modal
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            aria-label="测试连接"
            loading={testing}
            onClick={() => void testConnection()}
            theme="solid"
          >
            测试连接
          </Button>
          <Button
            aria-label="完成连接"
            disabled={!reachable || !hasSelection}
            loading={finishing}
            onClick={() => void finish()}
            theme="solid"
            type="primary"
          >
            完成连接
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title={refreshMode ? "刷新模型（重新发现）" : "连接模型服务"}
      visible={visible}
      width={560}
    >
      <div style={{ display: "grid", rowGap: 16 }}>
        {!refreshMode ? (
          <>
            <div>
              <Typography.Text>协议</Typography.Text>
              <Select
                aria-labelledby="connect-provider-protocol-label"
                disabled
                optionList={[{ label: "OpenAI Compatible", value: "openai-compatible" }]}
                style={{ width: "100%" }}
                value="openai-compatible"
              />
              <span className="sr-only" id="connect-provider-protocol-label">
                协议
              </span>
            </div>
            <div>
              <Typography.Text>名称 *</Typography.Text>
              <Input
                aria-label="模型服务名称"
                disabled={createdProvider !== null}
                onChange={(value) => setName(String(value))}
                placeholder="如：deepseek"
                value={name}
              />
            </div>
          </>
        ) : null}
        <div>
          <Typography.Text>Endpoint *</Typography.Text>
          <Input
            aria-label="Endpoint"
            disabled={createdProvider !== null}
            onChange={(value) => setEndpoint(String(value))}
            placeholder="https://api.example.com/v1"
            value={endpoint}
          />
        </div>
        <div>
          <Typography.Text>凭据 *</Typography.Text>
          <div style={{ display: "flex", gap: 8 }}>
            <span className="sr-only" id="connect-provider-credential-label">
              凭据选择
            </span>
            <Select
              aria-labelledby="connect-provider-credential-label"
              disabled={createdProvider !== null}
              filter
              onChange={(value) => setCredentialRef(String(value ?? ""))}
              optionList={credentialOptions.map((option) => ({
                label: option.label,
                value: option.value
              }))}
              placeholder="选择凭据（不可手填 raw ref）"
              style={{ flex: 1 }}
              value={credentialRef}
            />
            <Button
              aria-label="内嵌新增凭据"
              onClick={() => setCredentialModalVisible(true)}
              theme="light"
            >
              + 新增凭据
            </Button>
          </div>
        </div>

        {testError ? (
          <Typography.Text type="danger" role="alert">
            {testError}
          </Typography.Text>
        ) : null}
        {reachable ? (
          <div aria-label="发现模型列表" style={{ display: "grid", rowGap: 8 }}>
            <Typography.Text type="success">连接成功</Typography.Text>
            {discovered.length > 0 ? (
              discovered.map((model) => (
                <Checkbox
                  aria-label={`模型 ${model}`}
                  checked={selected.has(model)}
                  key={model}
                  onChange={(event) => toggleModel(model, Boolean(event.target.checked))}
                >
                  {model}
                </Checkbox>
              ))
            ) : (
              <Typography.Text type="tertiary">
                该服务不支持模型发现，可手工添加模型。
              </Typography.Text>
            )}
          </div>
        ) : null}

        <div>
          <Typography.Text>+ 手工添加模型（Provider 不支持发现时）</Typography.Text>
          <div style={{ display: "flex", gap: 8 }}>
            <Input
              aria-label="手工模型名"
              onChange={(value) => setManualInput(String(value))}
              placeholder="如：deepseek-chat"
              value={manualInput}
            />
            <Button aria-label="添加手工模型" onClick={addManualModel} theme="light">
              添加
            </Button>
          </div>
          {manualModels.length > 0 ? (
            <div aria-label="手工模型列表" style={{ marginTop: 8 }}>
              {manualModels.map((model) => (
                <Checkbox
                  aria-label={`模型 ${model}`}
                  checked
                  key={model}
                  onChange={(event) => toggleModel(model, Boolean(event.target.checked))}
                >
                  {model}
                </Checkbox>
              ))}
            </div>
          ) : null}
        </div>

        {error ? (
          <Typography.Text type="danger" role="alert">
            {error}
          </Typography.Text>
        ) : null}
      </div>
      <CreateCredentialModal
        api={api}
        onClose={() => setCredentialModalVisible(false)}
        onCreated={() => setCredentialModalVisible(false)}
        visible={credentialModalVisible}
      />
    </Modal>
  );
}
