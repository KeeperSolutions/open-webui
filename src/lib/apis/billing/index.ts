import { WEBUI_API_BASE_URL } from '$lib/constants';

export type BillingStatus = {
	enabled: boolean;
	is_configured: boolean;
	subscription_status: string | null;
	current_month_cost_eur: number;
	upcoming_invoice_eur: number | null;
	has_payment_method: boolean;
};

export type Invoice = {
	id: string;
	date: number;
	amount_eur: number;
	status: string;
	pdf_url: string | null;
	hosted_url: string | null;
};

const base = `${WEBUI_API_BASE_URL}/billing`;

async function request<T>(url: string, token: string, options: RequestInit = {}): Promise<T> {
	const res = await fetch(url, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	});
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw new Error(err.detail ?? 'Request failed');
	}
	return res.json();
}

export const getBillingStatus = (token: string) =>
	request<BillingStatus>(`${base}/status`, token);

export const setupBilling = (token: string) =>
	request<{ is_configured: boolean; subscription_status: string | null }>(
		`${base}/setup`,
		token,
		{ method: 'POST' }
	);

export const getBillingPortalUrl = (token: string) =>
	request<{ url: string }>(`${base}/portal`, token, { method: 'POST' });

export const getInvoices = (token: string) => request<Invoice[]>(`${base}/invoices`, token);

export type AdminBillingRow = {
	user_id: string;
	name: string;
	email: string;
	subscription_status: string | null;
	stripe_customer_id: string | null;
	current_month_cost_eur: number;
	free_tier_credit_applied: boolean;
};

export const getAdminBillingSummary = (token: string) =>
	request<AdminBillingRow[]>(`${base}/admin/summary`, token);
