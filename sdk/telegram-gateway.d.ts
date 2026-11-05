export interface GatewayOptions {
  gatewayUrl: string;
  secret?: string | null;
}

export interface ChatItem {
  id: number;
  title: string;
  username: string | null;
  type: "channel" | "megagroup" | "chat";
  unread_count: number;
}

export interface FeedItem {
  mid: number;
  title: string;
  desc: string;
  date: number;
  duration: number;
  type: "video" | "audio" | "photo" | "file";
  size: number;
  mime_type: string;
  filename: string | null;
  stream: string;
  stream_url: string;
  file_url: string;
  thumb: string;
  thumb_url: string;
  has_poster: boolean;
  poster_mid: number | null;
}

export interface MediaInfo {
  ok: boolean;
  chat_id: number;
  message_id: number;
  filename: string;
  size: number;
  mime_type: string;
  caption: string;
  date: number;
  stream_url: string;
  file_url: string;
  thumb_url: string;
}

export interface FeedOptions {
  limit?: number;
  offsetId?: number;
  type?: "video" | "audio" | "photo" | "document" | "all";
}

export class TelegramGateway {
  constructor(options: GatewayOptions);
  sign(chatId: number | string, messageId: number | string, exp: number): Promise<string>;
  getStreamUrl(chatId: number | string, messageId: number | string, ttlSeconds?: number): Promise<string>;
  getDownloadUrl(chatId: number | string, messageId: number | string, ttlSeconds?: number): Promise<string>;
  getThumbUrl(chatId: number | string, messageId: number | string): string;
  listChats(): Promise<ChatItem[]>;
  getFeed(chatId: number | string, options?: FeedOptions): Promise<FeedItem[]>;
  getInfo(chatId: number | string, messageId: number | string): Promise<MediaInfo>;
}

export default TelegramGateway;
