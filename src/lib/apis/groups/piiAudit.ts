import { WEBUI_API_BASE_URL } from '$lib/constants';

/**
 * The PII policy audit trail for one group.
 *
 * Deliberately its own module rather than another function in `./index.ts`:
 * that file is upstream, and every line we add there is a line the next upstream
 * merge has to reconcile. Nothing here is shared with the group CRUD calls.
 */

export type PiiPolicyAuditEventType =
	| 'policy_enabled'
	| 'policy_disabled'
	| 'member_added'
	| 'member_removed';

export type PiiPolicyAuditEvent = {
	id: string;
	event_type: PiiPolicyAuditEventType;
	group_id: string;
	/** Set for `member_*` only. */
	user_id?: string | null;
	/** Resolved at read time; null when that account no longer exists. */
	user_email?: string | null;
	actor_user_id: string;
	/** Stored on the row, so it survives the acting account being deleted. */
	actor_email: string;
	reason?: string | null;
	event_ts: number;
};

/** `items` is the newest slice; `total` is every event this group has. */
export type PiiPolicyAuditPage = {
	items: PiiPolicyAuditEvent[];
	total: number;
};

export const getGroupPiiAudit = async (
	token: string,
	id: string
): Promise<PiiPolicyAuditPage | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/groups/id/${id}/pii-audit`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
