export type PeerType = 'user' | 'group';

export interface ChannelEnvelope {
  channel: string;
  accountId: string;
  peerType: PeerType;
  peerId: string;
  messageId: string;
  conversationRef?: string;
  contentType: string;
  content: string;
  receivedAt: string;
}

export interface DeliveryCommand {
  executionId: string;
  routeId: string;
  eventType: string;
  contentRef: string;
  dedupeKey: string;
}

export interface ChannelAdapter {
  readonly channel: string;
  start(): Promise<void>;
  stop(): Promise<void>;
  send(command: DeliveryCommand): Promise<void>;
}
