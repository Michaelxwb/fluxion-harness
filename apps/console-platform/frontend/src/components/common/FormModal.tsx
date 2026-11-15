import { Form, Modal } from '@douyinfe/semi-ui';
import type { ButtonProps } from '@douyinfe/semi-ui/lib/es/button';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface FormModalProps {
  visible: boolean;
  title: ReactNode;
  width?: number;
  confirmLoading?: boolean;
  okText?: string;
  cancelText?: string;
  okButtonProps?: ButtonProps;
  onOk(): void;
  onCancel(): void;
  getFormApi?(api: FormApi): void;
  /** 打开时的初始值；调用方用 `key` 让弹窗在换目标时重挂载，初始值才会重新生效。 */
  initValues?: Record<string, unknown>;
  onSubmit?(values: Record<string, unknown>): void;
  children?: ReactNode;
}

export function FormModal(props: FormModalProps) {
  const { t } = useTranslation();
  return (
    <Modal
      centered
      className="app-form-modal"
      visible={props.visible}
      title={props.title}
      width={props.width ?? 520}
      style={{ maxWidth: 'calc(100vw - 32px)' }}
      confirmLoading={props.confirmLoading}
      okText={props.okText ?? t('common.confirm')}
      cancelText={props.cancelText ?? t('common.cancel')}
      okButtonProps={props.okButtonProps}
      cancelButtonProps={{ type: 'primary' }}
      onOk={props.onOk}
      onCancel={props.onCancel}
    >
      <Form<Record<string, unknown>>
        className="app-modal-form"
        labelPosition="top"
        labelAlign="left"
        initValues={props.initValues}
        getFormApi={props.getFormApi}
        onSubmit={props.onSubmit}
      >
        {props.children}
      </Form>
    </Modal>
  );
}
