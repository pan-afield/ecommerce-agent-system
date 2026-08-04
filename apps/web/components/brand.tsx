export function Brand() {
  return (
    <div className="flex items-center gap-3" aria-label="Relay Desk">
      <span className="grid size-9 place-items-center rounded-md bg-ink font-mono text-sm font-medium text-white">
        RD
      </span>
      <span>
        <span className="block text-sm font-bold text-ink">Relay Desk</span>
        <span className="block font-mono text-[10px] uppercase text-ink-muted">
          Agent workspace
        </span>
      </span>
    </div>
  );
}
