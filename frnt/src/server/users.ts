// Демо-сотрудники для фоллбека: работают, когда бэкенд недоступен.
//
// Смысл тот же, что у mock-движка: сайт на защите не должен зависеть от того, поднят ли
// FastAPI. Здесь — учётки в памяти процесса и сессии-токены, правила ролей повторяют
// бэкендовые (политика и сотрудники — только администратор).

import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

export type Role = "admin" | "manager";

export interface MockUser {
  id: string;
  login: string;
  name: string;
  role: Role;
  isActive: boolean;
  createdAt: string;
  lastLoginAt: string | null;
  secret: string; // scrypt-хеш пароля
}

interface State {
  users: MockUser[];
  sessions: Map<string, string>; // токен → id пользователя
  seq: number;
}

const globalScope = globalThis as unknown as { __LOS_USERS__?: State };

function hash(password: string, salt = randomBytes(16).toString("hex")): string {
  return `${salt}:${scryptSync(password, salt, 32).toString("hex")}`;
}

function verify(password: string, secret: string): boolean {
  const [salt, expected] = secret.split(":");
  if (!salt || !expected) return false;
  const actual = scryptSync(password, salt, 32);
  const stored = Buffer.from(expected, "hex");
  // длина проверяется до сравнения: timingSafeEqual бросает на разных длинах
  return actual.length === stored.length && timingSafeEqual(actual, stored);
}

function seed(): State {
  const now = new Date().toISOString();
  const users: MockUser[] = [
    { id: "1", login: "admin", name: "Администратор", role: "admin", isActive: true, createdAt: now, lastLoginAt: null, secret: hash("admin") },
    { id: "2", login: "manager", name: "Менеджер А.", role: "manager", isActive: true, createdAt: now, lastLoginAt: null, secret: hash("manager") },
  ];
  return { users, sessions: new Map(), seq: users.length };
}

function state(): State {
  if (!globalScope.__LOS_USERS__) globalScope.__LOS_USERS__ = seed();
  return globalScope.__LOS_USERS__;
}

export function listUsers(): MockUser[] {
  return state().users;
}

export function findByLogin(login: string): MockUser | undefined {
  return state().users.find((u) => u.login === login);
}

export function byToken(token: string | null): MockUser | null {
  if (!token) return null;
  const st = state();
  const userId = st.sessions.get(token);
  const user = userId ? st.users.find((u) => u.id === userId) : undefined;
  return user && user.isActive ? user : null;
}

export function login(loginValue: string, password: string): { token: string; user: MockUser } | "bad" | "inactive" {
  const user = findByLogin(loginValue);
  if (!user || !verify(password, user.secret)) return "bad";
  if (!user.isActive) return "inactive";
  const token = randomBytes(24).toString("base64url");
  state().sessions.set(token, user.id);
  user.lastLoginAt = new Date().toISOString();
  return { token, user };
}

export function logout(token: string | null): boolean {
  return token ? state().sessions.delete(token) : false;
}

export function revokeSessions(userId: string): void {
  for (const [token, id] of state().sessions) if (id === userId) state().sessions.delete(token);
}

export function createUser(input: { login: string; password: string; name?: string; role?: Role }): MockUser | string {
  const st = state();
  const loginValue = input.login.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]{2,31}$/.test(loginValue)) return "Логин: 3–32 символа, латиница/цифры";
  if ((input.password ?? "").length < 5) return "Пароль: минимум 5 символов";
  if (findByLogin(loginValue)) return "Такой логин уже занят";

  const user: MockUser = {
    id: String(++st.seq),
    login: loginValue,
    name: input.name?.trim() || loginValue,
    role: input.role === "admin" ? "admin" : "manager",
    isActive: true,
    createdAt: new Date().toISOString(),
    lastLoginAt: null,
    secret: hash(input.password),
  };
  st.users.push(user);
  return user;
}

export function patchUser(
  id: string,
  patch: { name?: string; role?: Role; password?: string; isActive?: boolean },
): MockUser | string {
  const st = state();
  const user = st.users.find((u) => u.id === id);
  if (!user) return "Сотрудник не найден";
  const admins = st.users.filter((u) => u.role === "admin" && u.isActive && u.id !== id).length;

  if (patch.role && user.role === "admin" && patch.role !== "admin" && admins === 0) return "В системе должен остаться администратор";
  if (patch.isActive === false && user.role === "admin" && admins === 0) return "В системе должен остаться администратор";
  if (patch.password !== undefined && (patch.password ?? "").length < 5) return "Пароль: минимум 5 символов";

  if (patch.name !== undefined) user.name = patch.name.trim() || user.login;
  if (patch.role !== undefined) user.role = patch.role;
  if (patch.isActive !== undefined) user.isActive = patch.isActive;
  if (patch.password !== undefined) user.secret = hash(patch.password);

  if (patch.password !== undefined || patch.role !== undefined || patch.isActive === false) revokeSessions(id);
  return user;
}

/**
 * Проверка роли для фоллбека: когда бэкенд недоступен, права обязан держать сайт —
 * иначе без FastAPI менеджер увидел бы политику, которую ему запрещено менять.
 * Возвращает готовый ответ-отказ или null, если доступ разрешён.
 */
export async function mockAdminGuard(): Promise<Response | null> {
  const { sessionToken } = await import("@/lib/session");
  const user = byToken(await sessionToken());
  if (!user) return Response.json({ detail: "Нужна авторизация: войдите по логину и паролю" }, { status: 401 });
  if (user.role !== "admin") return Response.json({ detail: "Доступно только администратору" }, { status: 403 });
  return null;
}

export function publicUser(user: MockUser) {
  const { secret: _secret, ...rest } = user;
  return rest;
}
