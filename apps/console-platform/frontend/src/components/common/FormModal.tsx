import { Form, Modal } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export interface FormModalProps {
  visible: boolean;
  title: ReactNode;
  confirmLoading?: boolean;
  okText?: string;
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
      onOk={props.onOk}
      onCancel={props.onCancel}
    >
      <Form>{props.children}</Form>
    </Modal>
  );
}
