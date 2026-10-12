/**
 * The text a failed request is reported with: its status and the server's
 * message — `detail` (a validation error's list: one `<field>: <msg>` per
 * entry), or `error` (the controller proxy's and a service's) — else the
 * status text, e.g. `400 Invalid scriptlet name 'a.b': ...`.
 */
export async function errorText(response: Response): Promise<string> {
    const body = await response.json().catch(() => undefined) as { detail?: unknown; error?: unknown } | undefined;
    let message = response.statusText;
    if (Array.isArray(body?.detail)) {
        message = body.detail
            .map((e: { loc: (string | number)[]; msg: string }) => `${e.loc.slice(1).join('.') || e.loc[0]}: ${e.msg}`)
            .join('; ');
    } else if (typeof body?.detail === 'string') {
        message = body.detail;
    } else if (typeof body?.error === 'string') {
        message = body.error;
    }
    return `${response.status} ${message}`;
}
