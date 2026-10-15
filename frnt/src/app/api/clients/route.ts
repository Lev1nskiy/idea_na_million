import { proxySite } from "@/server/backend";
import { CLIENT_OPTIONS } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Справочник клиентов для селектора на странице чата: список приходит с бэкенда,
 * поэтому он совпадает с тем, по кому реально работает движок. Если бэкенд не подключён —
 * отдаём прежний статический список (сайт остаётся работоспособным автономно).
 */
export async function GET() {
  const proxied = await proxySite("/clients");
  if (proxied) return proxied;

  return Response.json({ clients: CLIENT_OPTIONS });
}
