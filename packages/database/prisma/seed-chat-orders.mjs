import { resolve } from 'node:path';

import { PrismaPg } from '@prisma/adapter-pg';
import prismaPackage from '@prisma/client';
import { config } from 'dotenv';

const { Prisma, PrismaClient } = prismaPackage;

config({ path: resolve(import.meta.dirname, '../../../.env'), quiet: true });
config({ path: resolve(import.meta.dirname, '../.env'), override: true, quiet: true });

const connectionString = process.env.DATABASE_URL;
if (!connectionString) throw new Error('DATABASE_URL is required.');

const database = new URL(connectionString);
if (
  !['localhost', '127.0.0.1', '::1'].includes(database.hostname) ||
  decodeURIComponent(database.pathname.slice(1)) !== 'ecommerce_agents' ||
  process.env.NODE_ENV === 'production'
) {
  throw new Error('Chat demo orders may only be created in the local ecommerce_agents database.');
}

const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString }) });
const ownerId = 'demo-user-customer';
const batch = '20260922';

const orders = [
  { status: 'PAID', amount: '89.00', createdAt: '2026-09-20T01:00:00Z', events: [] },
  {
    status: 'SHIPPED', amount: '159.00', createdAt: '2026-09-19T02:00:00Z', events: [
      ['2026-09-19T03:00:00Z', 'confirmed', '商家已确认订单', '杭州市'],
      ['2026-09-19T09:00:00Z', 'packed', '商品已完成打包', '杭州市'],
      ['2026-09-20T04:00:00Z', 'shipped', '包裹已从杭州分拨中心发出', '杭州市'],
    ],
  },
  {
    status: 'DELIVERED', amount: '299.00', createdAt: '2026-09-16T02:00:00Z', events: [
      ['2026-09-16T03:00:00Z', 'confirmed', '商家已确认订单', '杭州市'],
      ['2026-09-17T05:00:00Z', 'shipped', '包裹已从杭州分拨中心发出', '杭州市'],
      ['2026-09-18T07:00:00Z', 'out_for_delivery', '派送员正在配送', '上海市'],
      ['2026-09-18T10:00:00Z', 'delivered', '包裹已由本人签收', '上海市'],
    ],
  },
  { status: 'PENDING', amount: '39.00', createdAt: '2026-09-21T01:00:00Z', events: [] },
  { status: 'CANCELLED', amount: '69.00', createdAt: '2026-09-18T01:00:00Z', events: [] },
  {
    status: 'SHIPPED', amount: '429.00', createdAt: '2026-09-19T01:00:00Z', events: [
      ['2026-09-19T03:00:00Z', 'confirmed', '商家已确认订单', '深圳市'],
      ['2026-09-20T06:00:00Z', 'shipped', '包裹已发往广州', '深圳市'],
    ],
  },
  {
    status: 'DELIVERED', amount: '88.00', createdAt: '2026-09-17T01:00:00Z', events: [
      ['2026-09-17T04:00:00Z', 'packed', '商品已完成打包', '北京市'],
      ['2026-09-18T07:00:00Z', 'shipped', '包裹已从北京分拨中心发出', '北京市'],
      ['2026-09-19T09:00:00Z', 'delivered', '包裹已由本人签收', '天津市'],
    ],
  },
  {
    status: 'PAID', amount: '199.00', createdAt: '2026-09-21T02:00:00Z', events: [
      ['2026-09-21T03:00:00Z', 'confirmed', '商家已确认订单，等待打包', '成都市'],
    ],
  },
  {
    status: 'SHIPPED', amount: '129.00', createdAt: '2026-09-18T02:00:00Z', events: [
      ['2026-09-18T04:00:00Z', 'confirmed', '商家已确认订单', '南京市'],
      ['2026-09-19T08:00:00Z', 'shipped', '包裹已发往苏州', '南京市'],
      ['2026-09-20T09:00:00Z', 'in_transit', '包裹正在运输途中', '苏州市'],
    ],
  },
  {
    status: 'DELIVERED', amount: '519.00', createdAt: '2026-09-15T01:00:00Z', events: [
      ['2026-09-15T04:00:00Z', 'confirmed', '商家已确认订单', '广州市'],
      ['2026-09-16T06:00:00Z', 'packed', '商品已完成打包', '广州市'],
      ['2026-09-17T07:00:00Z', 'shipped', '包裹已从广州分拨中心发出', '广州市'],
      ['2026-09-19T11:00:00Z', 'delivered', '包裹已由本人签收', '佛山市'],
    ],
  },
].map((order, index) => {
  const suffix = String(index + 1).padStart(3, '0');
  return {
    id: `order-chat-${batch}-${suffix}`,
    orderNumber: `EC-CHAT-${batch}-${suffix}`,
    userId: ownerId,
    status: order.status,
    totalAmount: new Prisma.Decimal(order.amount),
    currency: 'CNY',
    createdAt: new Date(order.createdAt),
    shipmentEvents: {
      create: order.events.map(([occurredAt, status, description, location], eventIndex) => ({
        id: `shipment-chat-${batch}-${suffix}-${eventIndex + 1}`,
        status,
        description,
        location,
        occurredAt: new Date(occurredAt),
      })),
    },
  };
});

async function main() {
  const owner = await prisma.user.findUnique({ where: { id: ownerId } });
  if (owner?.email !== 'customer.demo@example.com' || owner.role !== 'CUSTOMER') {
    throw new Error('The local customer.demo@example.com seed account is missing or mismatched.');
  }

  const existing = await prisma.order.findMany({
    where: { id: { in: orders.map((order) => order.id) } },
    select: {
      id: true,
      userId: true,
      refundApplications: { select: { id: true } },
      shipmentEvents: { select: { id: true } },
    },
  });
  if (process.argv.includes('--check')) {
    const refunds = existing.reduce((count, order) => count + order.refundApplications.length, 0);
    const shipments = existing.reduce((count, order) => count + order.shipmentEvents.length, 0);
    const wrongOwners = existing.filter((order) => order.userId !== ownerId).length;
    console.log(`Target: local ecommerce_agents; owner: ${owner.email}`);
    console.log(`Batch orders: ${existing.length}; shipment events: ${shipments}; refund applications: ${refunds}; wrong owners: ${wrongOwners}`);
    return;
  }
  if (existing.length > 0) {
    throw new Error(`Chat demo batch already contains ${existing.length} order(s); no data was changed.`);
  }

  await prisma.$transaction(async (transaction) => {
    for (const order of orders) await transaction.order.create({ data: order });
  });

  const created = await prisma.order.findMany({
    where: { id: { in: orders.map((order) => order.id) } },
    include: { refundApplications: { select: { id: true } }, shipmentEvents: { select: { id: true } } },
    orderBy: { id: 'asc' },
  });
  if (created.length !== 10 || created.some((order) => order.refundApplications.length > 0)) {
    throw new Error('Chat demo batch verification failed.');
  }
  for (const order of created) {
    console.log(`${order.id}  ${order.status}  CNY ${order.totalAmount}  物流 ${order.shipmentEvents.length} 条`);
  }
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : 'Failed to seed chat demo orders.');
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
