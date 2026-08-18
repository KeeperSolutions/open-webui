import { describe, it, expect } from 'vitest';
import { grantedModelIds, type AccessGrant, type ModelRecord } from './modelAccess';

const OWNER = 'owner-id';

const grant = (principal_type: string, principal_id: string, permission = 'read'): AccessGrant => ({
	principal_type,
	principal_id,
	permission
});

const model = (id: string, grants: AccessGrant[] = [], user_id = OWNER): ModelRecord => ({
	id,
	user_id,
	access_grants: grants
});

const subject = (id = 'u1', group_ids: string[] = []) => ({ id, group_ids });

describe('grantedModelIds', () => {
	it('grants a public model to everyone', () => {
		const out = grantedModelIds(subject(), [model('m1', [grant('user', '*')])]);
		expect([...out]).toEqual(['m1']);
	});

	it('grants a user-scoped model only to that user', () => {
		const models = [model('m1', [grant('user', 'u1')])];
		expect([...grantedModelIds(subject('u1'), models)]).toEqual(['m1']);
		expect([...grantedModelIds(subject('u2'), models)]).toEqual([]);
	});

	it('grants a group-scoped model to a member of that group', () => {
		const models = [model('m1', [grant('group', 'g1')])];
		expect([...grantedModelIds(subject('u1', ['g1']), models)]).toEqual(['m1']);
	});

	it('withholds a group-scoped model from a non-member', () => {
		const models = [model('m1', [grant('group', 'g1')])];
		expect([...grantedModelIds(subject('u1', ['g2']), models)]).toEqual([]);
	});

	it('grants an owned model even with no grants at all', () => {
		expect([...grantedModelIds(subject('u1'), [model('m1', [], 'u1')])]).toEqual(['m1']);
	});

	it('withholds an ungranted model from a non-owner', () => {
		expect([...grantedModelIds(subject('u1'), [model('m1', [], 'someone-else')])]).toEqual([]);
	});

	it('does not let a write grant imply read', () => {
		const models = [
			model('m1', [grant('user', 'u1', 'write')]),
			model('m2', [grant('user', '*', 'write')]),
			model('m3', [grant('group', 'g1', 'write')])
		];
		expect([...grantedModelIds(subject('u1', ['g1']), models)]).toEqual([]);
	});

	it('gives a user with no groups only public, personal and owned models', () => {
		const models = [
			model('public', [grant('user', '*')]),
			model('personal', [grant('user', 'u1')]),
			model('owned', [], 'u1'),
			model('grouped', [grant('group', 'g1')]),
			model('other', [grant('user', 'u2')])
		];
		expect([...grantedModelIds(subject('u1', []), models).values()].sort()).toEqual([
			'owned',
			'personal',
			'public'
		]);
	});

	it('handles a user whose group_ids are missing entirely', () => {
		const models = [model('m1', [grant('group', 'g1')])];
		expect([...grantedModelIds({ id: 'u1' }, models)]).toEqual([]);
		expect([...grantedModelIds({ id: 'u1', group_ids: null }, models)]).toEqual([]);
	});

	it('returns nothing for an empty catalogue', () => {
		expect(grantedModelIds(subject(), []).size).toBe(0);
	});

	it('counts a model reachable through several grants only once', () => {
		const models = [model('m1', [grant('user', 'u1'), grant('group', 'g1'), grant('user', '*')])];
		const out = grantedModelIds(subject('u1', ['g1']), models);
		expect(out.size).toBe(1);
		expect([...out]).toEqual(['m1']);
	});

	it('tolerates a model with no access_grants field', () => {
		expect(grantedModelIds(subject('u1'), [{ id: 'm1', user_id: 'other' }]).size).toBe(0);
		expect(
			grantedModelIds(subject('u1'), [{ id: 'm1', user_id: 'other', access_grants: null }]).size
		).toBe(0);
	});

	it('ignores a grant with an unknown principal_type', () => {
		const models = [model('m1', [grant('team', 'u1')])];
		expect([...grantedModelIds(subject('u1'), models)]).toEqual([]);
	});
});
