import type { MasteryRow } from "../api";

const pct = (v: number) => `${Math.round(v * 100)}%`;

const FIELDS: { label: string; get: (r: MasteryRow) => string }[] = [
  { label: "State", get: (r) => r.state },
  { label: "Recall", get: (r) => pct(r.recall_score) },
  { label: "Understanding", get: (r) => pct(r.understanding_score) },
  { label: "Visualization", get: (r) => pct(r.visualization_score) },
  { label: "Speed", get: (r) => pct(r.speed_score) },
  { label: "Reps", get: (r) => String(r.reps) },
  { label: "Interval (days)", get: (r) => String(r.interval_days) },
  { label: "Ease", get: (r) => r.ease_factor.toFixed(2) },
  { label: "Next due", get: (r) => new Date(r.due_at).toLocaleString() },
];

/** Before/after MasteryState for one position, changed fields highlighted. `after` is null until the answer is graded. */
export function MasteryDelta({ before, after }: { before: MasteryRow; after: MasteryRow | null }) {
  return (
    <table aria-label="Mastery before and after">
      <thead><tr><th></th><th>Before</th><th>After</th></tr></thead>
      <tbody>
        {FIELDS.map((f) => {
          const b = f.get(before);
          const a = after ? f.get(after) : null;
          return (
            <tr key={f.label}>
              <th scope="row">{f.label}</th>
              <td>{b}</td>
              <td data-changed={a != null && a !== b} style={a != null && a !== b ? { fontWeight: 700, color: "var(--accent)" } : undefined}>
                {a ?? "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
