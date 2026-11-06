/** Owner is supplied by the verified session, never by query parameters. */
export function usageFilter(params: URLSearchParams, owner: string) {
  const values: unknown[] = [owner];
  const clauses = ["auth_sub=$1"];
  for (const [key, column] of Object.entries({ service: "service", model: "model", status: "status" })) {
    const value = params.get(key);
    if (value) {
      if (value.length > 200) throw new Error("invalid filter");
      values.push(value);
      clauses.push(`${column}=$${values.length}`);
    }
  }
  const operation = params.get("operation");
  if (operation) {
    if (operation.length > 200) throw new Error("invalid operation");
    values.push(operation);
    clauses.push(`(operation_id=$${values.length} OR parent_operation_id=$${values.length})`);
  }
  for (const [key, operator] of [["from", ">="], ["to", "<"]]) {
    const value = params.get(key);
    if (value) {
      if (!/^\d{4}-\d{2}-\d{2}(T.*(?:Z|[+-]\d{2}:\d{2}))?$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("invalid date");
      values.push(new Date(value).toISOString());
      clauses.push(`created_at${operator}$${values.length}::timestamptz`);
    }
  }
  for (const key of ["experiment_id", "case_id", "arm_id", "run_id"]) {
    const value = params.get(key);
    if (value) {
      if (value.length > 200) throw new Error("invalid experiment filter");
      values.push(value);
      clauses.push(`links->>'${key}'=$${values.length}`);
    }
  }
  const offset = Number(params.get("offset") ?? 0);
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 100000) throw new Error("invalid offset");
  return { where: clauses.join(" AND "), values, offset };
}
