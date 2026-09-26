import { Camera, CirclePlay, Globe, MessageCircle, Rss, Send, type LucideIcon } from "lucide-react";
import type { SourceKind } from "@/lib/api";

export const KIND_ICON: Record<SourceKind, LucideIcon> = {
  telegram: Send, website: Globe, rss: Rss, instagram: Camera, youtube: CirclePlay, vk: MessageCircle,
};
export const KIND_LABELS: Record<SourceKind, string> = {
  telegram: "Telegram", website: "Сайт", rss: "RSS", instagram: "Instagram", youtube: "YouTube", vk: "VK",
};

const MEDIA_LABELS: Record<string, string> = {
  text: "текст", photo: "фото", album: "альбом", video: "видео", reel: "рилс", carousel: "карусель",
  document: "документ", poll: "опрос",
};
export const media = (v: string) => MEDIA_LABELS[v] ?? v;

export const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
export const fmtNum = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("ru-RU"));
export const pct = (n: number | null) => (n === null ? "—" : `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`);
export const label = (v: string | null) => (v ? v.replaceAll("_", " ") : null);
export const times = (n: number) => `×${n.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}`;
