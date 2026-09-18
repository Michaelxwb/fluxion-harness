import { Form, Modal } from '@douyinfe/semi-ui';
import type { ButtonProps } from '@douyinfe/semi-ui/lib/es/button';
import type { ReactNode } from 'react';

export interface FormModalProps {
  visible: boolean;
  title: ReactNode;
  confirmLoading?: boolean;
  okText?: string;
  okButtonProps?: ButtonProps;
  onOk(): void;
  onCancel(): void;
  children?: ReactNode;
}

export function FormModal(props: FormModalProps) {
  return (
    <Modal
      visible={props.visible}
      title={props.title}
      confirmLoading={props.confirmLoading}
      okText={props.okText}
      okButtonProps={props.okButtonProps}
      onOk={props.onOk}
      onCancel={props.onCancel}
    >
      <Form>{props.children}</Form>
    </Modal>
  );
}
