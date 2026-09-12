/**
 * 分类图标分组 —— 跟 app 端 `lib/widgets/biz/grouped_icon_grid.dart` 对齐
 * (267 个 stored 值,12 组支出 + 6 组收入;2026-09-12 从旧版
 * `icon_picker_page.dart`(已死代码,99 个)重新对齐到 app 目前实际在用的
 * 这份 picker)。
 *
 * 每个 item 的 `key` 是 **stored 值**(app DB 的 `categories.icon` 字段),
 * web 渲染时用 `resolveMaterialIconName` 走 FLUTTER_RENAMES 映射拿到真正的
 * Material Symbols 名;app 端读这个 key 走 `category_service.getCategoryIcon`
 * switch 拿 IconData。两端 stored 值完全一致,跨端兼容。
 *
 * 每个 icon 的中文 `label` 是这个文件手写的短标签(app 端 `grouped_icon_grid.dart`
 * 只有 key+IconData,没有对应的中文文案),纯粹给 picker 网格底下的小字
 * 跟搜索用,不走 i18n(维持跟舊版一致的做法)。
 *
 * 维护:动这个文件之前先看 app 端 `grouped_icon_grid.dart` 是不是也改了,
 * 两边必须同步。新增图标项必须保证 key 在 categoryIconMap.ts 的 KNOWN_NAMES
 * 或 FLUTTER_RENAMES 里有定义,否则 web 渲染会 fallback 到 'category' 字面图标。
 */

export type CategoryIconItem = {
  /** stored 值,跟 app categories.icon 字段对齐 */
  key: string
  /** 中文显示标签(picker grid 单元格底部的小字) */
  label: string
}

export type CategoryIconGroup = {
  /** i18n key,用 t() 翻译 group tab 的标题 */
  labelKey: string
  icons: CategoryIconItem[]
}

/** 支出类目分组(12 组,对齐 app grouped_icon_grid.dart expense 分支) */
export const EXPENSE_ICON_GROUPS: readonly CategoryIconGroup[] = [
  {
    labelKey: 'categories.iconGroup.basic',
    icons: [
      { key: 'category', label: '分类' },
      { key: 'label', label: '标签' },
      { key: 'bookmark', label: '收藏' },
      { key: 'star', label: '星标' },
      { key: 'favorite', label: '喜欢' },
      { key: 'circle', label: '圆点' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.dining',
    icons: [
      { key: 'restaurant', label: '餐厅' },
      { key: 'local_dining', label: '用餐' },
      { key: 'fastfood', label: '快餐' },
      { key: 'local_cafe', label: '咖啡厅' },
      { key: 'local_bar', label: '酒吧' },
      { key: 'local_pizza', label: '披萨' },
      { key: 'cake', label: '蛋糕' },
      { key: 'coffee', label: '咖啡' },
      { key: 'breakfast_dining', label: '早餐' },
      { key: 'lunch_dining', label: '午餐' },
      { key: 'dinner_dining', label: '晚餐' },
      { key: 'icecream', label: '冰淇淋' },
      { key: 'bakery_dining', label: '烘焙' },
      { key: 'liquor', label: '烈酒' },
      { key: 'wine_bar', label: '红酒' },
      { key: 'restaurant_menu', label: '菜单' },
      { key: 'set_meal', label: '套餐' },
      { key: 'ramen_dining', label: '拉面' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.transport',
    icons: [
      { key: 'directions_car', label: '汽车' },
      { key: 'directions_bus', label: '公交' },
      { key: 'directions_subway', label: '地铁' },
      { key: 'local_taxi', label: '出租车' },
      { key: 'flight', label: '飞机' },
      { key: 'train', label: '火车' },
      { key: 'motorcycle', label: '摩托车' },
      { key: 'directions_bike', label: '自行车' },
      { key: 'directions_walk', label: '步行' },
      { key: 'boat', label: '船' },
      { key: 'electric_scooter', label: '电动车' },
      { key: 'local_gas_station', label: '加油' },
      { key: 'local_parking', label: '停车' },
      { key: 'traffic', label: '路况' },
      { key: 'directions_railway', label: '铁路' },
      { key: 'airport_shuttle', label: '接驳车' },
      { key: 'pedal_bike', label: '单车' },
      { key: 'car_rental', label: '租车' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.shopping',
    icons: [
      { key: 'shopping_cart', label: '购物车' },
      { key: 'shopping_bag', label: '购物袋' },
      { key: 'store', label: '商店' },
      { key: 'local_mall', label: '商场' },
      { key: 'local_grocery_store', label: '超市' },
      { key: 'storefront', label: '店铺' },
      { key: 'shopping_basket', label: '购物篮' },
      { key: 'local_offer', label: '优惠' },
      { key: 'receipt', label: '收据' },
      { key: 'sell', label: '出售' },
      { key: 'price_check', label: '比价' },
      { key: 'card_giftcard', label: '礼品卡' },
      { key: 'redeem', label: '兑换' },
      { key: 'inventory', label: '库存' },
      { key: 'add_shopping_cart', label: '加购' },
      { key: 'loyalty', label: '会员卡' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.life',
    icons: [
      { key: 'home', label: '居家' },
      { key: 'house', label: '房屋' },
      { key: 'apartment', label: '公寓' },
      { key: 'cleaning_services', label: '清洁' },
      { key: 'plumbing', label: '水管' },
      { key: 'electrical_services', label: '电工' },
      { key: 'flash_on', label: '水电' },
      { key: 'water_drop', label: '水费' },
      { key: 'air', label: '空调' },
      { key: 'kitchen', label: '厨房' },
      { key: 'bathtub', label: '浴室' },
      { key: 'bed', label: '床' },
      { key: 'chair', label: '家具' },
      { key: 'table_restaurant', label: '餐桌' },
      { key: 'lightbulb', label: '电费' },
      { key: 'hvac', label: '暖通' },
      { key: 'roofing', label: '屋顶' },
      { key: 'foundation', label: '装修' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.devices',
    icons: [
      { key: 'phone', label: '通讯' },
      { key: 'smartphone', label: '手机' },
      { key: 'phone_android', label: '安卓机' },
      { key: 'phone_iphone', label: 'iPhone' },
      { key: 'tablet', label: '平板' },
      { key: 'laptop', label: '笔电' },
      { key: 'computer', label: '电脑' },
      { key: 'desktop_windows', label: '台式机' },
      { key: 'watch', label: '手表' },
      { key: 'headphones', label: '耳机' },
      { key: 'headset', label: '耳麦' },
      { key: 'keyboard', label: '键盘' },
      { key: 'mouse', label: '鼠标' },
      { key: 'wifi', label: '网络' },
      { key: 'router', label: '路由器' },
      { key: 'cable', label: '线材' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.entertainment',
    icons: [
      { key: 'movie', label: '电影' },
      { key: 'music_note', label: '音乐' },
      { key: 'sports_esports', label: '游戏' },
      { key: 'theater_comedy', label: '戏剧' },
      { key: 'casino', label: '博彩' },
      { key: 'celebration', label: '庆祝' },
      { key: 'party_mode', label: '派对' },
      { key: 'nightlife', label: '夜生活' },
      { key: 'local_activity', label: '活动票券' },
      { key: 'attractions', label: '游乐园' },
      { key: 'beach_access', label: '海滩' },
      { key: 'pool', label: '游泳池' },
      { key: 'spa', label: '美容' },
      { key: 'games', label: '桌游' },
      { key: 'sports', label: '运动' },
      { key: 'sports_soccer', label: '足球' },
      { key: 'sports_basketball', label: '篮球' },
      { key: 'sports_tennis', label: '网球' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.health',
    icons: [
      { key: 'local_hospital', label: '医院' },
      { key: 'medical_services', label: '医疗' },
      { key: 'local_pharmacy', label: '药店' },
      { key: 'health_and_safety', label: '保健' },
      { key: 'medication', label: '药品' },
      { key: 'fitness_center', label: '健身' },
      { key: 'self_improvement', label: '冥想' },
      { key: 'psychology', label: '心理' },
      { key: 'healing', label: '治疗' },
      { key: 'monitor_heart', label: '心电图' },
      { key: 'elderly', label: '长者照护' },
      { key: 'accessible', label: '无障碍' },
      { key: 'medical_information', label: '病历' },
      { key: 'biotech', label: '检验' },
      { key: 'coronavirus', label: '病毒' },
      { key: 'vaccines', label: '疫苗' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.education',
    icons: [
      { key: 'school', label: '学校' },
      { key: 'book', label: '书本' },
      { key: 'library_books', label: '图书馆' },
      { key: 'menu_book', label: '书籍' },
      { key: 'auto_stories', label: '教材' },
      { key: 'edit', label: '编辑' },
      { key: 'create', label: '写作' },
      { key: 'calculate', label: '计算' },
      { key: 'science', label: '科学' },
      { key: 'brush', label: '绘画' },
      { key: 'palette', label: '艺术' },
      { key: 'music_video', label: '音乐课' },
      { key: 'piano', label: '钢琴' },
      { key: 'translate', label: '翻译' },
      { key: 'language', label: '语言' },
      { key: 'quiz', label: '测验' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.pets',
    icons: [
      { key: 'pets', label: '宠物' },
      { key: 'cruelty_free', label: '爱心' },
      { key: 'bug_report', label: '昆虫' },
      { key: 'emoji_nature', label: '自然' },
      { key: 'park', label: '公园' },
      { key: 'grass', label: '草坪' },
      { key: 'forest', label: '森林' },
      { key: 'agriculture', label: '农业' },
      { key: 'eco', label: '生态' },
      { key: 'local_florist', label: '花卉' },
      { key: 'yard', label: '庭院' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.fashion',
    icons: [
      { key: 'checkroom', label: '服装' },
      { key: 'face', label: '护肤' },
      { key: 'face_retouching', label: '美颜' },
      { key: 'content_cut', label: '理发' },
      { key: 'dry_cleaning', label: '干洗' },
      { key: 'local_laundry_service', label: '洗衣' },
      { key: 'iron', label: '熨烫' },
      { key: 'diamond', label: '珠宝' },
      { key: 'watch_later', label: '稍后' },
      { key: 'ring_volume', label: '铃声' },
      { key: 'gesture', label: '手势' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.other',
    icons: [
      { key: 'business', label: '商务' },
      { key: 'work', label: '工作' },
      { key: 'camera_alt', label: '摄影' },
      { key: 'photo_camera', label: '相机' },
      { key: 'videocam', label: '录像' },
      { key: 'print', label: '打印' },
      { key: 'mail', label: '邮件' },
      { key: 'local_post_office', label: '邮局' },
      { key: 'public', label: '公共' },
      { key: 'place', label: '地点' },
      { key: 'location_on', label: '位置' },
      { key: 'map', label: '地图' },
      { key: 'explore', label: '探索' },
      { key: 'compass', label: '指南针' },
      { key: 'schedule', label: '日程' },
      { key: 'access_time', label: '时间' },
    ],
  },
] as const

/** 收入类目分组(6 组,对齐 app grouped_icon_grid.dart income 分支) */
export const INCOME_ICON_GROUPS: readonly CategoryIconGroup[] = [
  {
    labelKey: 'categories.iconGroup.basic',
    icons: [
      { key: 'category', label: '分类' },
      { key: 'label', label: '标签' },
      { key: 'bookmark', label: '收藏' },
      { key: 'star', label: '星标' },
      { key: 'favorite', label: '喜欢' },
      { key: 'circle', label: '圆点' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.workIncome',
    icons: [
      { key: 'work', label: '工作' },
      { key: 'business', label: '商务' },
      { key: 'business_center', label: '商务中心' },
      { key: 'engineering', label: '工程' },
      { key: 'design_services', label: '设计' },
      { key: 'construction', label: '建筑' },
      { key: 'code', label: '编程' },
      { key: 'developer_mode', label: '开发者' },
      { key: 'computer', label: '电脑' },
      { key: 'laptop', label: '笔电' },
      { key: 'biotech', label: '检验' },
      { key: 'science', label: '科学' },
      { key: 'psychology', label: '心理' },
      { key: 'medical_services', label: '医疗' },
      { key: 'school', label: '学校' },
      { key: 'gavel', label: '法律' },
      { key: 'balance', label: '天平' },
      { key: 'support_agent', label: '客服' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.finance',
    icons: [
      { key: 'account_balance', label: '银行' },
      { key: 'account_balance_wallet', label: '钱包余额' },
      { key: 'savings', label: '储蓄' },
      { key: 'trending_up', label: '上涨' },
      { key: 'trending_down', label: '下跌' },
      { key: 'show_chart', label: '走势' },
      { key: 'analytics', label: '分析' },
      { key: 'paid', label: '已付款' },
      { key: 'money', label: '现金' },
      { key: 'currency_exchange', label: '汇率' },
      { key: 'credit_card', label: '信用卡' },
      { key: 'payment', label: '付款' },
      { key: 'receipt_long', label: '账单' },
      { key: 'request_quote', label: '报价' },
      { key: 'monetization_on', label: '收益' },
      { key: 'price_change', label: '调价' },
      { key: 'euro', label: '欧元' },
      { key: 'yen', label: '日元' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.reward',
    icons: [
      { key: 'card_giftcard', label: '礼品卡' },
      { key: 'redeem', label: '兑换' },
      { key: 'wallet', label: '钱包' },
      { key: 'emoji_events', label: '奖杯' },
      { key: 'celebration', label: '庆祝' },
      { key: 'volunteer_activism', label: '捐赠' },
      { key: 'loyalty', label: '会员卡' },
      { key: 'military_tech', label: '勋章' },
      { key: 'workspace_premium', label: '认证' },
      { key: 'verified', label: '已验证' },
      { key: 'diamond', label: '珠宝' },
      { key: 'auto_awesome', label: '惊喜' },
      { key: 'new_releases', label: '新品' },
      { key: 'toll', label: '通行费' },
      { key: 'casino', label: '博彩' },
      { key: 'confirmation_number', label: '兑奖券' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.investment',
    icons: [
      { key: 'apartment', label: '公寓' },
      { key: 'real_estate_agent', label: '房产' },
      { key: 'home', label: '居家' },
      { key: 'house', label: '房屋' },
      { key: 'store', label: '商店' },
      { key: 'storefront', label: '店铺' },
      { key: 'factory', label: '工厂' },
      { key: 'agriculture', label: '农业' },
      { key: 'energy_savings_leaf', label: '节能' },
      { key: 'solar_power', label: '太阳能' },
      { key: 'oil_barrel', label: '石油' },
      { key: 'local_gas_station', label: '加油' },
      { key: 'electric_bolt', label: '电力' },
      { key: 'water_drop', label: '水费' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.other',
    icons: [
      { key: 'handshake', label: '合作' },
      { key: 'schedule', label: '日程' },
      { key: 'undo', label: '撤销' },
      { key: 'refresh', label: '刷新' },
      { key: 'autorenew', label: '自动续期' },
      { key: 'update', label: '更新' },
      { key: 'sync', label: '同步' },
      { key: 'published_with_changes', label: '发布' },
      { key: 'swap_horiz', label: '转换' },
      { key: 'compare_arrows', label: '对比' },
      { key: 'call_received', label: '收款' },
      { key: 'input', label: '转入' },
      { key: 'move_down', label: '下移' },
      { key: 'south', label: '汇入' },
      { key: 'call_made', label: '汇出' },
    ],
  },
] as const

/** 拍平后的所有 stored 值集合,搜索时用。 */
export const ALL_GROUPED_ICON_KEYS: readonly string[] = Object.freeze(
  Array.from(
    new Set([
      ...EXPENSE_ICON_GROUPS.flatMap((g) => g.icons.map((i) => i.key)),
      ...INCOME_ICON_GROUPS.flatMap((g) => g.icons.map((i) => i.key)),
    ]),
  ),
)

/** 按 kind 取分组数据 */
export function getIconGroupsByKind(
  kind: 'expense' | 'income' | string,
): readonly CategoryIconGroup[] {
  return kind === 'income' ? INCOME_ICON_GROUPS : EXPENSE_ICON_GROUPS
}

