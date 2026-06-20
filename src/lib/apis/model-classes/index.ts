import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface ModelClass {
	id: number;
	name: string;
	models: string[] | null;
	credit_burn: number;
	msgs_pro?: string | null;
	msgs_premium?: string | null;
	msgs_business?: string | null;
	created_at: number;
	updated_at: number;
	order: number;
}

export interface ModelClassForm {
	name: string;
	models?: string[] | null;
	credit_burn: number;
	msgs_pro?: string | null;
	msgs_premium?: string | null;
	msgs_business?: string | null;
	order?: number | null;
}

export const getModelClasses = async (token: string = ''): Promise<ModelClass[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/model-classes/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const createModelClass = async (token: string, formData: ModelClassForm): Promise<ModelClass> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/model-classes/`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(formData)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateModelClass = async (
	token: string,
	id: number,
	formData: ModelClassForm
): Promise<ModelClass> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/model-classes/${id}`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(formData)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteModelClass = async (token: string, id: number): Promise<{ message: string }> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/model-classes/${id}`, {
		method: 'DELETE',
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
			error = err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export interface ReorderItem {
	id: number;
	order: number;
}

export const reorderModelClasses = async (
	token: string,
	items: ReorderItem[]
): Promise<ModelClass[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/model-classes/reorder`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(items)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
