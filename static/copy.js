/* GoGather 聚行 · 全站文案表
 *
 * 术语约定（全站唯一叫法，改文案前先看这里）：
 *   方案   —— 出行方案，不要写「计划」
 *   地点   —— 地图上的一个点，不要写「位置」「行程点」
 *   出发地 —— 不要写「出发」「起点」
 *   第二人称一律用「你」，不用「您」
 *   省略号一律用「…」，不用三个点「...」
 *
 * 取值：t('empty.plans', '原文案')
 *   第二个参数是回退值 —— 字典里没有这个 key 时原样返回，
 *   所以可以按批次增量迁移，不会出现界面变空白。
 *
 * 静态 HTML 用 data-copy="key" data-copy-fallback="原文案"，由 hydrateCopy() 统一填充；
 * 动态拼接的文案直接在模板字符串里写 ${t('key')}。
 */
window.COPY = {
  brand: {
    name: 'GoGather 聚行',
    tagline: '说清一场活动，AI 排好所有人的行程',
    domain: 'gogather.partystack.cn',
  },

  nav: {
    planTab: '策划',
    execTab: '实施',
    guide: '使用指南',
  },

  plan: {
    myPlans: '我的方案',
    newPlan: '新建方案',
    unnamed: '未命名方案',
    share: '分享方案',
    regenerate: '重新生成',
    delete: '删除方案',
    regenerateConfirm: '将用原来的输入条件重新生成一份方案，确定继续？',
    regenerating: '正在重新生成…',
    regenerateToast: '正在按原条件重新生成方案…',
    shareGenerated: '分享链接已生成',
    shareFailed: '分享失败，请稍后重试',
    generated: '方案已生成',
    revised: '方案已修改',
    removed: '方案已删除',
    removeFailed: '删除失败，请稍后重试',
    selectFirst: '请先在左侧选择一个方案',
    copyFailed: '复制失败，请手动复制地址栏链接',
    copied: '已复制到剪贴板',
  },

  empty: {
    plans: '还没有方案',
    plansHint: '在下方描述你要办的活动，AI 会给出完整出行方案',
    exec: '还没有可选的方案',
    execHint: '先到「策划」里生成一份方案，再回来跟踪执行进度',
    execBtn: '去策划页',
    timeline: '暂无时间线',
    timelineHint: '方案生成后，这里会按天列出每个地点、时间与花费',
    todo: '暂无待办事项',
    todoHint: '把需要提前确认的事项勾掉，这里会同步进度',
    reminder: '暂无提醒',
    reminderHint: '方案里的关键节点会自动变成提醒，出发前不用再翻聊天记录',
    revisions: '暂无修订记录',
    revisionsHint: '每次调整方案后，这里会保留旧版本，随时可以回滚',
    coords: '暂无可标记的地点坐标',
    coordsHint: '方案里出现具体地点后，这里会自动画出完整路线',
    adminUsers: '还没有注册用户',
    adminPlans: '还没有生成过方案',
    adminUsage: '近 30 天还没有 AI 调用记录',
  },

  loading: {
    thinking: '正在思考…',
    analyzing: '正在分析需求…',
    generating: '正在生成方案…',
    queryingFlight: '正在查询飞猪交通信息…',
    queryingPrice: '正在查询飞猪实时价格…',
    queryingRestaurant: '正在查询飞猪餐厅数据…',
    searchingHotel: '正在搜索酒店…',
    searchingSpot: '正在查找景点和活动…',
    computingBudget: '正在计算预算…',
    revisions: '加载中…',
  },

  toast: {
    needInput: '请先描述一下这次活动',
    loginExpired: '登录已过期，请重新登录',
    requestFailed: '请求失败，请检查网络后重试',
  },

  share: {
    readonly: '只读分享',
    loading: '加载方案中…',
    notFoundTitle: '方案不存在',
    missingCode: '缺少分享码',
    mapFailed: '地图加载失败，请稍后重试',
    ctaSub: '一句话说清你的活动，AI 帮你安排一切',
    ctaBtn: '立即开始规划',
    footer: '由 GoGather 聚行生成 · 出行数据来自飞猪',
  },

  error: {
    notFound: '页面不存在',
    shareInvalid: '分享链接无效或已过期',
    loadFailed: '加载失败',
    retry: '重试',
    reminderLoad: '提醒加载失败',
    reminderLoadHint: '可能是网络波动，点下面重试',
    loadUsers: '用户数据加载失败，请稍后重试',
    loadPlans: '方案数据加载失败，请稍后重试',
    loadPlanUsage: '该方案的 AI 使用详情加载失败，请稍后重试',
    nicknameTaken: '该昵称已被占用，换一个试试',
    nicknameInvalid: '昵称需为 2-20 位中英文、数字、下划线、点或减号',
  },

  tour: {
    menuEntry: '新手引导',
    skip: '跳过',
    next: '下一步',
    prev: '上一步',
    done: '开始规划',
    s1Title: '所有方案都在这里',
    s1Desc: '每次生成的方案都会存进左侧列表，点一下就能切回来。手机上从左上角菜单打开。',
    s2Title: '用一句话说清这次活动',
    s2Desc: '人数、日期、出发地、预算说得越具体，AI 给的方案越贴近实际。它会先给大纲，你确认后再生成完整行程。',
    s3Title: '不知道怎么开头？',
    s3Desc: '点这些预设场景快速起个草稿，再补上人数和日期就行。下面三个筛选可以设定预算档次和交通方式。',
  },

guide: {
    navEntry: '使用指南',
    entryHint: '看看使用指南',
  },

  auth: {
    welcomeTitle: '欢迎使用 GoGather 聚行',
    welcomeSub: '登录后即可开始规划你的活动出行',
    tabLogin: '登录',
    tabRegister: '注册',
    labelUsername: '昵称',
    labelPassword: '密码',
    labelConfirm: '确认密码',
    placeholderUsername: '输入昵称',
    placeholderPassword: '输入密码',
    placeholderConfirm: '再次输入密码',
    withGitHub: '使用 GitHub 登录',
    withAlipay: '使用支付宝登录',
    or: '或',
    usernameMin: '昵称至少 2 个字符',
    passwordMin: '密码至少 4 个字符',
    mismatch: '两次输入的密码不一致',
    noPermission: '当前账号没有管理后台权限',
    loadFailed: '概览数据加载失败，请稍后重试',
    nicknameTitle: '修改昵称',
    nicknameSub: '2-20 位中英文、数字、下划线、点或减号',
    nicknameSave: '保存',
    nicknameSaving: '保存中…',
    nicknameEmpty: '昵称不能为空',
    nicknameDone: '昵称已更新',
    logout: '退出登录',
    editNickname: '修改昵称',
  },
};

window.t = function (path, fallback = '') {
  const val = path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), window.COPY);
  return typeof val === 'string' ? val : fallback;
};

/* 填充静态 HTML 上标了 data-copy / data-copy-ph 的节点。未迁移的节点保持原样。 */
window.hydrateCopy = function (root = document) {
  root.querySelectorAll('[data-copy]').forEach((el) => {
    const fallback = el.dataset.copyFallback != null ? el.dataset.copyFallback : el.textContent.trim();
    el.textContent = t(el.dataset.copy, fallback);
  });
  root.querySelectorAll('[data-copy-ph]').forEach((el) => {
    el.placeholder = t(el.dataset.copyPh, el.placeholder);
  });
};

document.addEventListener('DOMContentLoaded', () => window.hydrateCopy());