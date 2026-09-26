import {
  BarChart3,
  Building2,
  CreditCard,
  Factory,
  FileSearch,
  Gauge,
  Globe,
  KeyRound,
  Layers,
  Lightbulb,
  Mic2,
  Newspaper,
  Plug,
  Rss,
  Swords,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { href: string; title: string; icon: LucideIcon; epic?: number; description?: string };

export const MAIN_NAV: NavItem[] = [
  { href: "/dashboard", title: "Обзор", icon: Gauge, epic: 3,
    description: "KPI рынка за 7/30/90 дней, динамика публикаций, растущие темы, лучшие публикации и рекомендации." },
  { href: "/competitors", title: "Конкуренты", icon: Swords, epic: 4,
    description: "Конкуренты и их источники, AI-профиль: позиционирование, ЦА, темы, форматы, tone of voice, CTA." },
  { href: "/feed", title: "Лента", icon: Rss, epic: 5,
    description: "Публикации рынка с фильтрами по теме, формату, воронке, CTA и overperformance; AI-разбор поста." },
  { href: "/topics", title: "Темы", icon: Layers, epic: 6,
    description: "Кластеры тем и под-тем, динамика, насыщенность рынка и Content Gaps с объяснением." },
  { href: "/websites", title: "Сайты", icon: Globe, epic: 7,
    description: "Изменения страниц услуг, блогов и офферов конкурентов: «было/стало» и смысл изменения." },
  { href: "/digest", title: "Дайджест", icon: Newspaper, epic: 11,
    description: "Еженедельный обзор рынка: новые темы, изменения конкурентов, лучшие публикации, рекомендации." },
  { href: "/audit", title: "Аудит контента", icon: FileSearch, epic: 8,
    description: "Оценка вашего контента по 6 критериям со сравнением с рынком, проблемы, gaps и 10 тем." },
  { href: "/strategy", title: "Стратегия", icon: Lightbulb, epic: 9,
    description: "10 тем, о которых стоит писать: почему, цифры рынка, ваш пробел, примеры конкурентов, форматы." },
  { href: "/factory", title: "Контент Завод", icon: Factory, epic: 10,
    description: "Тема → формат → brand voice → черновик → редактор → QA. Telegram, Email, LinkedIn, VK, статья." },
];

export const SETTINGS_NAV: NavItem[] = [
  { href: "/settings/company", title: "Компания", icon: Building2, epic: 3,
    description: "Ниша, темы и роли аудитории — справочник, по которому размечается контент рынка." },
  { href: "/settings/sources", title: "Источники", icon: Plug, epic: 2,
    description: "Ваши каналы и сайты: Telegram, сайт, RSS, VK, YouTube, Instagram. Статус синхронизации." },
  { href: "/settings/brand-voice", title: "Brand Voice", icon: Mic2, epic: 10,
    description: "Тон, запрещённые слова, примеры текстов — модель пишет вашим голосом." },
  { href: "/settings/team", title: "Команда", icon: Users },
  { href: "/settings/plan", title: "Тариф", icon: CreditCard },
  { href: "/settings/usage", title: "Использование", icon: BarChart3 },
  { href: "/settings/api", title: "API", icon: KeyRound, description: "API-ключи для Enterprise — после биллинга." },
];

export const METRIC_LABELS: Record<string, string> = {
  members: "Участники",
  competitors: "Конкуренты",
  sources: "Источники",
  websites: "Сайты",
  website_pages: "Страниц на сайт",
  audits_month: "Аудиты в месяц",
  generations_month: "Генерации в месяц",
  ai_cost_usd_month: "Расход AI в месяц, $",
};

export const ROLE_LABELS = { owner: "Владелец", admin: "Администратор", member: "Участник", viewer: "Наблюдатель" };
