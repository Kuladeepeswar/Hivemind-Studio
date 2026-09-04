// Render's blueprint fills VITE_API_URL from the api service's `host` property, which
// is a bare hostname like "hivemind-api.onrender.com" with no scheme. Accept both that
// and a full URL so the same code works locally and on Render.
const withScheme = (value, scheme) => {
  if (!value) return value;
  const stripped = value.replace(/^https?:\/\//, '').replace(/^wss?:\/\//, '').replace(/\/+$/, '');
  return `${scheme}://${stripped}`;
};

export const getApiUrl = () => {
  const configured = import.meta.env.VITE_API_URL;
  if (configured) return withScheme(configured, 'https');
  return `http://${window.location.hostname}:3000`;
};

// Derived from the API URL by default. Keeping one source of truth avoids the failure
// where the two point at different hosts and only the websocket silently breaks.
export const getWsUrl = () => {
  const configured = import.meta.env.VITE_WS_URL;
  if (configured) return withScheme(configured, 'wss');
  const api = import.meta.env.VITE_API_URL;
  if (api) return withScheme(api, 'wss');
  return `ws://${window.location.hostname}:3000`;
};

// Anonymous identity, used for creator attribution and one-like-per-person.
export const getSessionId = () => {
  let id = localStorage.getItem('session_id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('session_id', id);
  }
  return id;
};
