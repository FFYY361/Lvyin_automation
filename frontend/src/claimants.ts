import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { UserSummary } from "./types";

export function useClaimantNames(values: Array<number | null | undefined>): Record<number, string> {
  const key = useMemo(
    () => Array.from(new Set(values.filter((value): value is number => typeof value === "number"))).sort((a, b) => a - b).join(","),
    [values],
  );
  const [names, setNames] = useState<Record<number, string>>({});

  useEffect(() => {
    const ids = key ? key.split(",").map(Number) : [];
    let active = true;
    if (!ids.length) {
      setNames({});
      return () => { active = false; };
    }
    void Promise.all(ids.map(async (id) => {
      try {
        const user = await api<UserSummary>(`/api/admin/users/${id}`);
        return [id, user.display_name] as const;
      } catch {
        return [id, `用户 #${id}`] as const;
      }
    })).then((entries) => { if (active) setNames(Object.fromEntries(entries)); });
    return () => { active = false; };
  }, [key]);

  return names;
}
