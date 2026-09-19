"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { listAgreements, listExternalParties, listLegalEntities, listSignatories } from "@/lib/api";

interface Contact {
  key: string;
  name: string;
  email: string | null;
  title: string | null;
  company: string;
  kind: "internal" | "counterparty";
  authority?: string | null;
  agreements: Array<{ id: string; title: string; status: string }>;
}

const kindTone: Record<Contact["kind"], string> = {
  internal: "bg-blue-100 text-blue-800",
  counterparty: "bg-emerald-100 text-emerald-800",
};

export default function ContactsPage() {
  const { token } = useAuth();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"all" | Contact["kind"]>("all");

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [entities, agreements] = await Promise.all([
        listLegalEntities(token).catch(() => []),
        listAgreements(token).catch(() => []),
      ]);

      const internal = await Promise.all(
        entities.map(async (entity) => {
          const signatories = await listSignatories(token, entity.id).catch(() => []);
          return signatories.map<Contact>((s) => ({
            key: `int-${s.id}`,
            name: s.name,
            email: null,
            title: s.title,
            company: entity.legal_name,
            kind: "internal",
            authority:
              s.authority_type === "unlimited"
                ? "Unlimited authority"
                : s.maximum_value != null
                  ? `Up to ${s.maximum_value.toLocaleString()} ${s.currency ?? ""}`.trim()
                  : s.authority_type,
            agreements: [],
          }));
        })
      );

      const external = new Map<string, Contact>();
      await Promise.all(
        agreements.map(async (a) => {
          const parties = await listExternalParties(token, a.id).catch(() => []);
          for (const p of parties) {
            const k = `ext-${p.signatory_email.toLowerCase()}`;
            const existing = external.get(k);
            const ref = { id: a.id, title: a.title, status: a.status };
            if (existing) existing.agreements.push(ref);
            else
              external.set(k, {
                key: k,
                name: p.signatory_name,
                email: p.signatory_email,
                title: null,
                company: p.company_name,
                kind: "counterparty",
                agreements: [ref],
              });
          }
        })
      );

      setContacts(
        [...internal.flat(), ...Array.from(external.values())].sort((a, b) => a.name.localeCompare(b.name))
      );
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load contacts");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    queueMicrotask(() => load());
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return contacts.filter(
      (c) =>
        (kind === "all" || c.kind === kind) &&
        (!q || [c.name, c.email, c.company, c.title].some((v) => v?.toLowerCase().includes(q)))
    );
  }, [contacts, query, kind]);

  const counts = useMemo(
    () => ({
      internal: contacts.filter((c) => c.kind === "internal").length,
      counterparty: contacts.filter((c) => c.kind === "counterparty").length,
    }),
    [contacts]
  );

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contacts</h1>
          <p className="text-sm text-gray-500">
            Authorised signatories for your legal entities and every counterparty signatory you have dealt with.
          </p>
        </div>
        <div className="flex gap-3 text-sm">
          <Link href="/companies" className="rounded-md border border-gray-300 px-3 py-2 text-gray-700 hover:bg-gray-50">
            Companies
          </Link>
          <Link href="/signature-authority" className="rounded-md bg-blue-600 px-3 py-2 font-medium text-white hover:bg-blue-700">
            Manage signatories
          </Link>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, email, company…"
          className="w-full max-w-sm rounded-md border border-gray-300 px-3 py-2 text-sm"
        />
        <div className="flex rounded-md border border-gray-300 text-sm">
          {(
            [
              ["all", `All (${contacts.length})`],
              ["internal", `Our signatories (${counts.internal})`],
              ["counterparty", `Counterparties (${counts.counterparty})`],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`px-3 py-1.5 transition first:rounded-l-md last:rounded-r-md ${
                kind === k ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      {loading ? (
        <div className="py-16 text-center text-gray-500">Loading contacts…</div>
      ) : filtered.length === 0 ? (
        <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center text-sm text-gray-500">
          No contacts match.
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((c) => (
            <div key={c.key} className="group rounded-lg bg-white p-5 shadow transition hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gray-100 text-sm font-semibold text-gray-700">
                    {c.name
                      .split(/\s+/)
                      .slice(0, 2)
                      .map((w) => w[0]?.toUpperCase())
                      .join("")}
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-900">{c.name}</div>
                    <div className="text-xs text-gray-500">{c.title || c.company}</div>
                  </div>
                </div>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${kindTone[c.kind]}`}>
                  {c.kind === "internal" ? "signatory" : "counterparty"}
                </span>
              </div>
              <dl className="mt-4 space-y-1 text-xs text-gray-600">
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-400">Company</dt>
                  <dd className="truncate text-right">{c.company}</dd>
                </div>
                {c.email && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-gray-400">Email</dt>
                    <dd className="truncate text-right">
                      <a href={`mailto:${c.email}`} className="hover:text-blue-600">
                        {c.email}
                      </a>
                    </dd>
                  </div>
                )}
                {c.authority && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-gray-400">Authority</dt>
                    <dd className="text-right">{c.authority}</dd>
                  </div>
                )}
              </dl>
              {c.agreements.length > 0 && (
                <div className="mt-4 border-t border-gray-100 pt-3">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                    {c.agreements.length} agreement{c.agreements.length === 1 ? "" : "s"}
                  </div>
                  <ul className="space-y-1">
                    {c.agreements.slice(0, 3).map((a) => (
                      <li key={a.id}>
                        <Link href={`/agreements/${a.id}`} className="block truncate text-xs text-blue-600 hover:underline">
                          {a.title} <span className="text-gray-400">· {a.status.replace(/_/g, " ")}</span>
                        </Link>
                      </li>
                    ))}
                    {c.agreements.length > 3 && (
                      <li className="text-xs text-gray-400">+{c.agreements.length - 3} more</li>
                    )}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
