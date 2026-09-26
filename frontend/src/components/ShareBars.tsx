/** Доля темы у рынка и у клиента — полосами (Content Gap). */
export function ShareBars({ own, market }: { own: number; market: number }) {
  return (
    <div className="w-full space-y-1">
      <div className="flex items-center gap-2 text-xs">
        <span className="w-12 text-muted">рынок</span>
        <div className="h-2 flex-1 rounded-full bg-bg"><div className="h-2 rounded-full bg-warn" style={{ width: `${market}%` }} /></div>
        <span className="w-12 text-right">{market}%</span>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span className="w-12 text-muted">вы</span>
        <div className="h-2 flex-1 rounded-full bg-bg"><div className="h-2 rounded-full bg-accent" style={{ width: `${own}%` }} /></div>
        <span className="w-12 text-right">{own}%</span>
      </div>
    </div>
  );
}
