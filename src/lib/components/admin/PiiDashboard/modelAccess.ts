/**
 * Which models a user may read, computed from the same inputs the backend uses.
 *
 * Mirrors `has_access()` in backend/open_webui/utils/access_control/__init__.py:
 * grants are matched on an exact `permission`, so a `write` grant does not imply
 * `read`. `BYPASS_ADMIN_ACCESS_CONTROL` is deliberately not modelled — the column
 * reports *configured* access, and folding the bypass in would make every admin
 * read "All models" and say nothing.
 */

export type AccessGrant = {
	principal_type: string;
	principal_id: string;
	permission: string;
};

export type ModelRecord = {
	id: string;
	user_id: string;
	access_grants?: AccessGrant[] | null;
};

export type AccessSubject = {
	id: string;
	group_ids?: string[] | null;
};

const READ = 'read';
/** A grant on this principal id means "everyone", not "a user called *". */
const EVERYONE = '*';

export function grantedModelIds(subject: AccessSubject, models: ModelRecord[]): Set<string> {
	const groupIds = new Set(subject.group_ids ?? []);
	const granted = new Set<string>();

	for (const model of models) {
		// Ownership stands on its own: get_filtered_models lets an owner through
		// whatever the grants say.
		if (model.user_id && model.user_id === subject.id) {
			granted.add(model.id);
			continue;
		}

		for (const grant of model.access_grants ?? []) {
			if (grant?.permission !== READ) continue;

			const reachable =
				grant.principal_type === 'user'
					? grant.principal_id === EVERYONE || grant.principal_id === subject.id
					: grant.principal_type === 'group' && groupIds.has(grant.principal_id);

			if (reachable) {
				// A Set, so a model reachable through several grants counts once.
				granted.add(model.id);
				break;
			}
		}
	}

	return granted;
}
