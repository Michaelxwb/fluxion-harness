import { Form, Modal } from '@douyinfe/semi-ui';
import type { ButtonProps } from '@douyinfe/semi-ui/lib/es/button';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import type { ReactNode } from 'react';

export interface FormModalProps {
  visible: boolean;
  title: ReactNode;
  width?: number;
  confirmLoading?: boolean;
  okText?: string;
  okButtonProps?: ButtonProps;
  onOk(): void;
  onCancel(): void;
  getFormApi?(api: FormApi): void;
  onSubmit?(values: Record<string, unknown>): void;
  children?: ReactNode;
}

export function FormModal(props: FormModalProps) {
  return (
    <Modal
      visible={props.visible}
      title={props.title}
      width={props.width}
      confirmLoading={props.confirmLoading}
      okText={props.okText}
      okButtonProps={props.okButtonProps}
      onOk={props.onOk}
      onCancel={props.onCancel}
    >
      <Form<Record<string, unknown>> getFormApi={props.getFormApi} onSubmit={props.onSubmit}>
        {props.children}
      </Form>
    </Modal>
  );
}
