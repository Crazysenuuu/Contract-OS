"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import {
  listBillingPlans,
  BillingPlan,
  subscribeToPlan,
  changeSubscription,
  getSubscription,
  Subscription,
  cancelSubscription,
  getEntitlements,
  Entitlement,
  recordUsage,
  checkUsage,
  listInvoices,
  Invoice,
  issueInvoice,
  payInvoice,
} from "@/lib/api";

export default function BillingPage() {
  const { token, user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [entitlements, setEntitlements] = useState<Entitlement[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Usage
  const [usageFeature, setUsageFeature] = useState("");
  const [usageCheck, setUsageCheck] = useState<Entitlement | null>(null);
  const [recordFeature, setRecordFeature] = useState("");
  const [recordQty, setRecordQty] = useState(1);

  // Invoice issue
  const [invDescription, setInvDescription] = useState("");
  const [invQty, setInvQty] = useState(1);
  const [invUnitPrice, setInvUnitPrice] = useState(0);

  const reload = useCallback(() => {
    if (!token) return;
    setError(null);
    Promise.all([
      listBillingPlans(token),
      getEntitlements(token),
      listInvoices(token),
      getSubscription(token).catch(() => null),
    ])
      .then(([pl, ent, inv, sub]) => {
        setPlans(pl);
        setEntitlements(ent.entitlements);
        setInvoices(inv);
        setSubscription(sub);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load billing data"));
  }, [token]);

  useEffect(() => {
    // Defer so the effect body never triggers a synchronous setState cascade.
    queueMicrotask(() => reload());
  }, [reload]);

  const run = async (fn: () => Promise<unknown>, successMsg: string, after?: () => void) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      after?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    }
  };

  const handlePickPlan = (plan: BillingPlan) => {
    if (!token) return;
    const action = subscription ? changeSubscription : subscribeToPlan;
    run(
      () => action(token, { plan_code: plan.code }),
      subscription
        ? `Subscription changed to ${plan.name}`
        : `Subscribed to ${plan.name}`,
      reload
    );
  };

  const handleUsageCheck = async () => {
    if (!token || !usageFeature.trim()) return;
    setError(null);
    try {
      setUsageCheck(await checkUsage(token, usageFeature.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Usage check failed");
    }
  };

  const handleRecordUsage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !recordFeature.trim()) return;
    run(
      () =>
        recordUsage(token, {
          feature_key: recordFeature.trim(),
          quantity: recordQty,
        }),
      "Usage recorded"
    );
  };

  const handleIssueInvoice = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !invDescription.trim()) return;
    run(
      () =>
        issueInvoice(token, {
          lines: [
            {
              description: invDescription.trim(),
              quantity: invQty,
              unit_price_cents: invUnitPrice,
            },
          ],
        }),
      "Invoice issued",
      reload
    ).then(() => {
      setInvDescription("");
      setInvQty(1);
      setInvUnitPrice(0);
    });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <Link href="/dashboard" className="text-sm text-gray-500 hover:text-gray-700">
              ← Dashboard
            </Link>
            <h1 className="text-2xl font-bold text-gray-900 mt-1">Billing &amp; Entitlements</h1>
            <p className="text-sm text-gray-500 mt-1">
              Plans, subscriptions, usage metering and invoices (spec 1.24) — billing state stays
              strictly separate from legal state.
            </p>
          </div>
          {subscription && (
            <button
              onClick={() =>
                run(() => cancelSubscription(token!), "Subscription canceled", reload)
              }
              className="px-4 py-2 text-sm font-medium text-rose-700 bg-white border border-rose-300 rounded-md hover:bg-rose-50"
            >
              Cancel subscription
            </button>
          )}
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md bg-rose-50 border border-rose-200 text-sm text-rose-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 px-4 py-3 rounded-md bg-emerald-50 border border-emerald-200 text-sm text-emerald-800">
            {notice}
          </div>
        )}

        {/* Current subscription */}
        <div className="bg-white shadow rounded-lg p-6 mb-6">
          <h2 className="text-lg font-medium text-gray-900 mb-2">Current Subscription</h2>
          {subscription ? (
            <div className="flex flex-wrap items-center gap-6">
              <div>
                <div className="text-xs text-gray-500 uppercase tracking-wider">Plan</div>
                <div className="text-xl font-bold text-gray-900 mt-0.5">
                  {subscription.plan_code ?? "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500 uppercase tracking-wider">Status</div>
                <span className={`mt-1 inline-block px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${
                  subscription.status === "active"
                    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                    : "bg-gray-100 text-gray-600 border-gray-200"
                }`}>
                  {subscription.status}
                </span>
              </div>
              <div>
                <div className="text-xs text-gray-500 uppercase tracking-wider">Period</div>
                <div className="text-sm text-gray-900 mt-0.5">
                  {subscription.current_period_start
                    ? new Date(subscription.current_period_start).toLocaleDateString()
                    : "—"}{" "}
                  →{" "}
                  {subscription.current_period_end
                    ? new Date(subscription.current_period_end).toLocaleDateString()
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500 uppercase tracking-wider">Seats</div>
                <div className="text-sm text-gray-900 mt-0.5">{subscription.seat_limit ?? "unlimited"}</div>
              </div>
              {subscription.external_provider && (
                <div>
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Provider</div>
                  <div className="text-sm text-gray-900 mt-0.5">
                    {subscription.external_provider}
                    {subscription.external_subscription_id ? ` (${subscription.external_subscription_id})` : ""}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              No active subscription. Pick a plan below to get started.
            </p>
          )}
        </div>

        {/* Plans */}
        <h2 className="text-lg font-medium text-gray-900 mb-3">Plans</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          {plans.length === 0 && (
            <p className="text-sm text-gray-500 col-span-full">No plans available.</p>
          )}
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`bg-white shadow rounded-lg p-6 flex flex-col ${
                subscription?.plan_code === plan.code ? "ring-2 ring-green-500" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-gray-900">{plan.name}</h3>
                {subscription?.plan_code === plan.code && (
                  <span className="px-2 py-0.5 text-[10px] uppercase font-bold rounded-full bg-green-100 text-green-700">
                    current
                  </span>
                )}
              </div>
              <p className="text-sm text-gray-500 mt-1 min-h-[2.5rem]">{plan.description ?? "—"}</p>
              <div className="text-2xl font-extrabold text-gray-900 mt-2">
                {(plan.monthly_price_cents / 100).toFixed(2)}
                <span className="text-sm font-medium text-gray-500"> {plan.currency}/mo</span>
              </div>
              <ul className="mt-4 space-y-1.5 flex-1">
                {Object.entries(plan.features).map(([key, value]) => (
                  <li key={key} className="text-xs text-gray-600 flex items-center">
                    <span className="text-green-600 mr-1.5">✓</span>
                    {key.replace(/_/g, " ")}
                    <span className="ml-auto text-gray-400">
                      {value === null ? "unlimited" : String(value)}
                    </span>
                  </li>
                ))}
              </ul>
              {/* Renewal disclosure (FTC Negative Option Rule / ROSCA):
                  the auto-renewal terms and the cancellation path must be
                  clear and conspicuous immediately next to the subscribe
                  action, before the consumer consents. */}
              <div
                aria-label="Subscription renewal terms and cancellation instructions"
                className="mt-3 rounded-md bg-amber-50 border border-amber-200 px-3 py-2.5"
              >
                <p className="text-[11px] leading-relaxed text-amber-900">
                  <strong>Auto-renews monthly.</strong> Your subscription
                  renews automatically each month at{" "}
                  <strong>
                    {(plan.monthly_price_cents / 100).toFixed(2)} {plan.currency}/mo
                  </strong>{" "}
                  until you cancel. You can cancel anytime from{" "}
                  <strong>Billing → Cancel subscription</strong> — no phone
                  call or email required. Cancellation takes effect
                  immediately and ends access to paid features.
                </p>
              </div>
              <button
                onClick={() => handlePickPlan(plan)}
                disabled={subscription?.plan_code === plan.code}
                className={`mt-5 w-full px-4 py-2 text-sm font-medium rounded-md ${
                  subscription?.plan_code === plan.code
                    ? "text-gray-400 bg-gray-100 cursor-default"
                    : "text-white bg-green-600 hover:bg-green-700"
                }`}
              >
                {subscription?.plan_code === plan.code
                  ? "Current plan"
                  : subscription
                    ? "Switch to this plan"
                    : "Subscribe with auto-renewal"}
              </button>
              {!subscription && (
                <p className="mt-2 text-[11px] text-gray-500">
                  By subscribing you agree to the auto-renewal terms above.
                </p>
              )}
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {/* Entitlements */}
          <div className="bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-medium text-gray-900">Entitlements</h2>
            </div>
            {entitlements.length === 0 ? (
              <p className="px-6 py-8 text-sm text-gray-500">
                No entitlements — subscribe to a plan first.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Feature</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Enabled</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Limit</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Source</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {entitlements.map((e) => (
                      <tr key={e.feature_key} className="hover:bg-gray-50">
                        <td className="px-6 py-3 text-sm font-medium text-gray-900">
                          {e.feature_key.replace(/_/g, " ")}
                        </td>
                        <td className="px-6 py-3">
                          <span className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded-full ${
                            e.enabled ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"
                          }`}>
                            {e.enabled ? "yes" : "no"}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-sm text-gray-600">
                          {e.limit === null ? "unlimited" : e.limit}
                        </td>
                        <td className="px-6 py-3 text-xs text-gray-400">{e.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Usage */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">Usage Metering</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Check a feature</label>
                <div className="flex gap-2">
                  <input
                    value={usageFeature}
                    onChange={(e) => setUsageFeature(e.target.value)}
                    placeholder="e.g. agreements_montly"
                    className="flex-1 rounded-md border-gray-300 shadow-sm text-sm"
                  />
                  <button
                    onClick={handleUsageCheck}
                    className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
                  >
                    Check
                  </button>
                </div>
                {usageCheck && (
                  <div className={`mt-2 px-3 py-2 rounded-md border text-sm ${
                    usageCheck.allowed
                      ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                      : "bg-amber-50 border-amber-200 text-amber-800"
                  }`}>
                    {usageCheck.allowed ? "Allowed" : "Not allowed"} —{" "}
                    {usageCheck.remaining === null ? "unlimited" : `${usageCheck.remaining} remaining`}
                    {usageCheck.limit !== null ? ` of ${usageCheck.limit}` : ""}
                  </div>
                )}
              </div>
              <form onSubmit={handleRecordUsage} className="border-t border-gray-200 pt-4">
                <label className="block text-xs font-medium text-gray-600 mb-1">Record usage</label>
                <div className="flex gap-2">
                  <input
                    value={recordFeature}
                    onChange={(e) => setRecordFeature(e.target.value)}
                    placeholder="feature key"
                    className="flex-1 rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                  <input
                    type="number"
                    min={1}
                    value={recordQty}
                    onChange={(e) => setRecordQty(Number(e.target.value))}
                    className="w-20 rounded-md border-gray-300 shadow-sm text-sm"
                  />
                  <button
                    type="submit"
                    className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700"
                  >
                    Record
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>

        {/* Invoices */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-medium text-gray-900">Invoices ({invoices.length})</h2>
            </div>
            {invoices.length === 0 ? (
              <p className="px-6 py-12 text-sm text-gray-500 text-center">No invoices yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Number</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Amount</th>
                      <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Due</th>
                      <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {invoices.map((inv) => (
                      <tr key={inv.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{inv.number}</td>
                        <td className="px-6 py-4">
                          <span className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border ${
                            inv.status === "paid"
                              ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                              : inv.status === "overdue"
                                ? "bg-rose-100 text-rose-800 border-rose-200"
                                : "bg-amber-100 text-amber-800 border-amber-200"
                          }`}>
                            {inv.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm font-bold text-gray-900">
                          {(inv.amount_cents / 100).toFixed(2)} {inv.currency}
                        </td>
                        <td className="px-6 py-4 text-xs text-gray-500">
                          {inv.due_date ? new Date(inv.due_date).toLocaleDateString() : "—"}
                        </td>
                        <td className="px-6 py-4 text-right">
                          {inv.status === "issued" && (
                            <button
                              onClick={() =>
                                run(() => payInvoice(token!, inv.id), "Invoice marked paid", reload)
                              }
                              className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
                            >
                              Mark paid
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {isAdmin && (
            <div className="bg-white shadow rounded-lg p-6 h-fit">
              <h2 className="text-lg font-medium text-gray-900 mb-4">Issue Invoice</h2>
              <form onSubmit={handleIssueInvoice} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Description *</label>
                  <input
                    value={invDescription}
                    onChange={(e) => setInvDescription(e.target.value)}
                    className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    required
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Qty</label>
                    <input
                      type="number"
                      min={1}
                      value={invQty}
                      onChange={(e) => setInvQty(Number(e.target.value))}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Unit price (cents)</label>
                    <input
                      type="number"
                      min={0}
                      value={invUnitPrice}
                      onChange={(e) => setInvUnitPrice(Number(e.target.value))}
                      className="w-full rounded-md border-gray-300 shadow-sm text-sm"
                    />
                  </div>
                </div>
                <button
                  type="submit"
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-md hover:bg-green-700"
                >
                  Issue invoice
                </button>
              </form>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}