import { PrismaPg } from '@prisma/adapter-pg';
import prismaPackage from '@prisma/client';

const { Prisma, PrismaClient } = prismaPackage;
const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
  throw new Error('DATABASE_URL is required to seed demo order data.');
}

const adapter = new PrismaPg({ connectionString });
const prisma = new PrismaClient({ adapter });

async function main() {
  const liMing = await prisma.user.upsert({
    where: { id: 'demo-user-li' },
    update: {
      name: '李明',
      email: 'li.ming@example.com',
      role: 'CUSTOMER',
      passwordHash:
        '$argon2id$v=19$m=65536,t=3,p=4$BSp3cW2zyILTV2ohP2RftA$pO4qVM8T9k8cNuDEATOXHVVzGZ9tCguBlgY8T+wlXmU',
    },
    create: {
      id: 'demo-user-li',
      name: '李明',
      email: 'li.ming@example.com',
      passwordHash:
        '$argon2id$v=19$m=65536,t=3,p=4$BSp3cW2zyILTV2ohP2RftA$pO4qVM8T9k8cNuDEATOXHVVzGZ9tCguBlgY8T+wlXmU',
    },
  });

  const wangFang = await prisma.user.upsert({
    where: { id: 'demo-user-wang' },
    update: {
      name: '王芳',
      email: 'wang.fang@example.com',
      role: 'CUSTOMER',
    },
    create: {
      id: 'demo-user-wang',
      name: '王芳',
      email: 'wang.fang@example.com',
      role: 'CUSTOMER',
    },
  });

  // Local-only accounts for manually verifying role-based knowledge visibility.
  // Passwords are represented by Argon2id hashes and are documented in the
  // development README, never returned by an API response.
  const roleAccounts = [
    {
      id: 'demo-user-customer',
      name: 'Customer Demo',
      email: 'customer.demo@example.com',
      passwordHash:
        '$argon2id$v=19$m=65536,t=3,p=4$tO+T+7zvOJCcqlYfbLuJ/w$rKc34Sg2DZnXln7MmExg4VXe/GkdOFnhU7PZaAi6X0I',
      role: 'CUSTOMER',
    },
    {
      id: 'demo-user-support',
      name: 'Support Demo',
      email: 'support.demo@example.com',
      passwordHash:
        '$argon2id$v=19$m=65536,t=3,p=4$olozgslKPq0hESJy+Pm/+A$X8UV+uM/ZjGbMkWC3fKYGn3DS9otQ81IlzSbNgiQARo',
      role: 'SUPPORT',
    },
    {
      id: 'demo-user-admin',
      name: 'Admin Demo',
      email: 'admin.demo@example.com',
      passwordHash:
        '$argon2id$v=19$m=65536,t=3,p=4$UGxl1dKgY0aEa/135P3vFw$hl6IYevdGpYwCy/p6gRZTLCZEAqyXdmfULJuZFLkdt8',
      role: 'ADMIN',
    },
  ];

  for (const account of roleAccounts) {
    await prisma.user.upsert({
      where: { id: account.id },
      update: account,
      create: account,
    });
  }

  const liMingOrder = await prisma.order.upsert({
    where: { id: 'order-demo-001' },
    update: {
      userId: liMing.id,
      status: 'SHIPPED',
      totalAmount: new Prisma.Decimal('299.00'),
      currency: 'CNY',
    },
    create: {
      id: 'order-demo-001',
      userId: liMing.id,
      orderNumber: 'EC-20260810-001',
      status: 'SHIPPED',
      totalAmount: new Prisma.Decimal('299.00'),
      currency: 'CNY',
    },
  });

  const wangFangOrder = await prisma.order.upsert({
    where: { id: 'order-demo-002' },
    update: {
      userId: wangFang.id,
      status: 'DELIVERED',
      totalAmount: new Prisma.Decimal('599.00'),
      currency: 'CNY',
    },
    create: {
      id: 'order-demo-002',
      userId: wangFang.id,
      orderNumber: 'EC-20260810-002',
      status: 'DELIVERED',
      totalAmount: new Prisma.Decimal('599.00'),
      currency: 'CNY',
    },
  });

  const shipmentEvents = [
    {
      id: 'shipment-event-003',
      orderId: liMingOrder.id,
      status: 'shipped',
      description: '包裹已从上海分拨中心发出',
      location: '上海市',
      occurredAt: new Date('2026-08-09T03:30:00.000Z'),
    },
    {
      id: 'shipment-event-001',
      orderId: liMingOrder.id,
      status: 'confirmed',
      description: '商家已确认订单',
      location: '杭州市',
      occurredAt: new Date('2026-08-08T01:15:00.000Z'),
    },
    {
      id: 'shipment-event-002',
      orderId: liMingOrder.id,
      status: 'packed',
      description: '商品已完成打包',
      location: '杭州市',
      occurredAt: new Date('2026-08-08T08:45:00.000Z'),
    },
    {
      id: 'shipment-event-004',
      orderId: wangFangOrder.id,
      status: 'delivered',
      description: '包裹已由本人签收',
      location: '北京市',
      occurredAt: new Date('2026-08-09T10:20:00.000Z'),
    },
  ];

  for (const event of shipmentEvents) {
    await prisma.shipmentEvent.upsert({
      where: { id: event.id },
      update: event,
      create: event,
    });
  }
}

main()
  .catch(() => {
    console.error('Failed to seed demo order data.');
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
