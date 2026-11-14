type Props = { mode?: string };

export function ModeBadge({ mode = "dry_run" }: Props) {
  const label = mode.replace("_", "-").toUpperCase();
  const cls =
    mode === "live" ? "badge badge-live" : mode === "demo" ? "badge badge-demo" : "badge badge-dry";
  return <span className={cls}>{label}</span>;
}
