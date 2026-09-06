cube(`Orders`, {
  sql_table: `main.orders`,

  joins: {
    Customers: {
      relationship: `many_to_one`,
      sql: `${CUBE}.customer_id = ${Customers}.customer_id`
    }
  },

  measures: {
    revenue: {
      sql: `amount`,
      type: `sum`,
      description: `Gross order revenue`
    },
    revenue_per_customer: {
      sql: `${revenue} / ${Customers.count}`,
      type: `number`,
      description: `Requires metric-to-metric composition`
    }
  },

  dimensions: {
    order_id: {
      sql: `order_id`,
      type: `number`,
      primaryKey: true
    },
    status: {
      sql: `status`,
      type: `string`
    }
  }
});

cube(`Customers`, {
  sql_table: `main.customers`,

  measures: {
    count: {
      type: `count`
    }
  },

  dimensions: {
    customer_id: {
      sql: `customer_id`,
      type: `number`,
      primaryKey: true
    }
  }
});
