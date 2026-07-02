import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { PlanTier } from '$lib/billing/planTiers';

export type BillingStatus = {
	enabled: boolean;
	plan_tier: PlanTier | null;
	is_configured: boolean;

	// Trial
	credit_limit_eur: number;
	credit_used_eur: number;
	credit_remaining_eur: number;

	// Paid / team subscription
	subscription_status: string | null;
	upcoming_invoice_eur: number | null;

	// Team owner fields
	team_id: string | null;
	team_name: string | null;
	seat_limit: number | null;
	seat_used: number | null;
	team_month_cost_eur: number | null;

	// Team usage budget (owner + member view)
	subscription_credits: number;
	topup_credits: number;
	credits_remaining: number;

	// Team member fields
	team_owner_name: string | null;

	// All tiers
	current_month_cost_eur: number;
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

export const createCheckoutSession = (token: string) =>
	request<{ url: string }>(`${base}/checkout`, token, { method: 'POST' });

export const getBillingPortalUrl = (token: string) =>
	request<{ url: string }>(`${base}/portal`, token, { method: 'POST' });

export const getInvoices = (token: string) => request<Invoice[]>(`${base}/invoices`, token);

export type AdminBillingRow = {
	user_id: string;
	name: string;
	email: string;
	plan_tier: string | null;
	subscription_status: string | null;
	stripe_customer_id: string | null;
	current_month_cost_eur: number;
	free_tier_credit_applied: boolean;
};

export const getAdminBillingSummary = (token: string) =>
	request<AdminBillingRow[]>(`${base}/admin/summary`, token);

// --- My usage (sidebar) ---

export type ExchangeRateEntry = {
	usd_per_eur: number;
	from: number; // unix epoch
	to: number;   // unix epoch
};

export type MyUsage = {
	month: number;
	year: number;
	total_tokens: number;
	total_cost_usd: number;
	total_cost_eur: number;
	exchange_rates: ExchangeRateEntry[];
	ledger_ready: boolean;
	credits_balance: number;
	credits_used: number;
	credits_remaining: number;
	credits_per_eur_cent: number; // 0 = internal (credits not applicable)
};

export const getMyUsage = (token: string) => request<MyUsage>(`${base}/my-usage`, token);

// --- Model breakdown ---

export type ModelBreakdownItem = {
	model: string;
	cost: number;
	pct: number;
};

export type ModelBreakdown = {
	models: ModelBreakdownItem[];
	total: number;
	month: string;
};

export const getModelBreakdown = (token: string) =>
	request<ModelBreakdown>(`${base}/model-breakdown`, token);

// --- Team types ---

export type TeamMember = {
	user_id: string;
	name: string;
	email: string;
	role: string;
	current_month_cost_eur: number;
	models_used: string[];
};

export type TeamInvite = {
	id: string;
	invited_email: string;
	status: string;
	token: string;
	expires_at: number;
};

export type TeamStatus = {
	team_id: string;
	name: string;
	seat_limit: number;
	seat_used: number;
	subscription_status: string | null;
	members: TeamMember[];
	pending_invites: TeamInvite[];
	team_month_cost_eur: number;
};

export type InviteInfo = {
	team_id: string;
	team_name: string;
	owner_name: string | null;
	invited_email: string;
	expires_at: number;
};

// --- Team API functions ---

export type TeamTier = {
	seat_count: number;
	price_eur: number;
};

export const getTeamTiers = (token: string) =>
	request<TeamTier[]>(`${base}/team/tiers`, token);

export const createTeam = (token: string, name: string, seat_count: number) =>
	request<{ url: string }>(`${base}/team/create`, token, {
		method: 'POST',
		body: JSON.stringify({ name, seat_count })
	});

export const getTeamStatus = (token: string) =>
	request<TeamStatus>(`${base}/team`, token);

export const inviteTeamMember = (token: string, email: string) =>
	request<{ invite_id: string; token: string; invited_email: string; expires_at: number }>(
		`${base}/team/invite`,
		token,
		{ method: 'POST', body: JSON.stringify({ email }) }
	);

export const removeTeamMember = (token: string, userId: string) =>
	request<{ removed: boolean }>(`${base}/team/members/${userId}`, token, { method: 'DELETE' });

export const getTeamPortalUrl = (token: string) =>
	request<{ url: string }>(`${base}/team/portal`, token, { method: 'POST' });

export const getInviteInfo = (token: string, inviteToken: string) =>
	request<InviteInfo>(`${base}/invite/${inviteToken}`, token);

export const acceptInvite = (token: string, inviteToken: string) =>
	request<{ accepted: boolean; team_id: string; team_name: string }>(
		`${base}/invite/${inviteToken}/accept`,
		token,
		{ method: 'POST' }
	);

export const declineInvite = (token: string, inviteToken: string) =>
	request<{ declined: boolean }>(`${base}/invite/${inviteToken}/decline`, token, {
		method: 'POST'
	});

// --- Top-up ---

export type TopupOption = {
	price_id: string;
	amount_eur: number;
};

export const getTopupOptions = (token: string) =>
	request<TopupOption[]>(`${base}/team/topup/options`, token);

export const createTeamTopup = (token: string, amount_eur: number) =>
	request<{ url: string }>(`${base}/team/topup`, token, {
		method: 'POST',
		body: JSON.stringify({ amount_eur })
	});
