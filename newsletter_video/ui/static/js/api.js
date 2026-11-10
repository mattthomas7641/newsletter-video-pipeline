const token = document.querySelector('meta[name="ui-token"]')?.content ?? "";

export class ApiError extends Error {
  constructor(message, status, fields) {
    super(message);
    this.status = status;
    this.fields = fields || {};
  }
}

async function request(method, path, { json, form } = {}) {
  const headers = { "X-UI-Token": token };
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form; // the browser sets the multipart boundary
  }
  let response;
  try {
    response = await fetch(path, { method, headers, body, cache: "no-store" });
  } catch {
    throw new ApiError("The control panel isn't responding. Is it still running in your terminal?", 0);
  }
  let data = {};
  try { data = await response.json(); } catch { /* empty or non-JSON body */ }
  if (!response.ok) {
    throw new ApiError(data.error || `Something went wrong (${response.status}).`, response.status, data.fields);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  put: (path, json) => request("PUT", path, { json }),
  post: (path, json = {}) => request("POST", path, { json }),
  upload: (path, form) => request("POST", path, { form }),
};
