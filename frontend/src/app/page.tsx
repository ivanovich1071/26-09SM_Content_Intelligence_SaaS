import Link from "next/link";

const STEPS = ["Аудит контента", "Проблемы и Content Gaps", "10 тем от рынка", "Готовые тексты"];

export default function Landing() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center px-6 py-16">
      <p className="text-sm font-semibold text-accent">AI Content Intelligence</p>
      <h1 className="mt-3 text-4xl font-extrabold leading-tight sm:text-5xl">
        Узнайте, что работает на вашем рынке — и что писать следующим
      </h1>
      <p className="mt-5 max-w-2xl text-lg text-muted">
        Сервис следит за конкурентами и нишей, находит темы, которые растут и которые никто не раскрыл,
        оценивает ваш контент и превращает это в готовые посты, письма и статьи.
      </p>
      <ol className="mt-8 flex flex-wrap gap-2 text-sm">
        {STEPS.map((s, i) => (
          <li key={s} className="rounded-full border border-line bg-surface px-3 py-1">
            {i + 1}. {s}
          </li>
        ))}
      </ol>
      <div className="mt-10 flex flex-wrap gap-3">
        <Link href="/register" className="btn">Бесплатный аудит контента</Link>
        <Link href="/login" className="btn-ghost">Войти</Link>
      </div>
    </main>
  );
}
