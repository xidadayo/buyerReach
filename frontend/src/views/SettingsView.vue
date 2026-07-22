<template>
  <div class="page-heading">
    <div>
      <h1 class="page-title">系统配置</h1>
      <span class="muted">按业务规则、数据服务和访问权限分区管理，修改后立即生效</span>
    </div>
    <el-button :loading="pageLoading" @click="reloadCurrentTab">刷新当前设置</el-button>
  </div>

  <el-tabs v-model="activeTab" class="settings-tabs">
    <el-tab-pane v-if="canReadSettings" label="业务规则" name="rules">
      <div class="panel">
        <div class="panel-heading"><div><h3>职位匹配规则</h3><p class="section-help">每行填写一个职位。系统会优先寻找 P1，随后依次使用 P2、P3；排除词始终优先。</p></div></div>
        <el-form label-position="top" :model="systemRules" class="settings-grid settings-grid--two">
          <el-form-item label="P1 · 首选决策人"><el-input v-model="systemRules.p1" type="textarea" :rows="3" placeholder="例如：Head of Buying&#10;Purchasing Director" /></el-form-item>
          <el-form-item label="P2 · 次选联系人"><el-input v-model="systemRules.p2" type="textarea" :rows="3" placeholder="例如：Senior Buyer&#10;Category Manager" /></el-form-item>
          <el-form-item label="P3 · 补充联系人"><el-input v-model="systemRules.p3" type="textarea" :rows="3" placeholder="例如：Buyer&#10;Merchandiser" /></el-form-item>
          <el-form-item label="不联系的职位"><el-input v-model="systemRules.excluded" type="textarea" :rows="3" placeholder="例如：Intern&#10;Assistant" /></el-form-item>
        </el-form>
      </div>
      <div class="panel">
        <div class="panel-heading"><div><h3>邮箱与任务执行</h3><p class="section-help">使用安全默认值控制邮箱判定、失败重试和任务处理规模。</p></div></div>
        <el-form label-position="top" :model="systemRules" class="settings-grid settings-grid--three">
          <el-form-item label="判定为有效（分）"><el-input-number v-model="systemRules.validScore" :min="0" :max="100" controls-position="right" /></el-form-item>
          <el-form-item label="判定为风险（分）"><el-input-number v-model="systemRules.riskyScore" :min="0" :max="100" controls-position="right" /></el-form-item>
          <el-form-item label="每个品牌联系人上限"><el-input-number v-model="systemRules.defaultContactLimit" :min="1" :max="50" controls-position="right" /></el-form-item>
          <el-form-item label="失败重试次数"><el-input-number v-model="systemRules.maxAttempts" :min="0" :max="10" controls-position="right" /></el-form-item>
          <el-form-item label="重试间隔（秒）"><el-input-number v-model="systemRules.retryDelay" :min="0" :max="3600" controls-position="right" /></el-form-item>
          <el-form-item label="同时处理的任务数"><el-input-number v-model="systemRules.maxConcurrency" :min="1" :max="50" controls-position="right" /></el-form-item>
        </el-form>
      </div>
      <div class="panel">
        <div class="panel-heading"><h3>AI 协调器</h3></div>
        <el-alert title="AI 只生成可审阅的搜索方案，不会自动执行任务或发送邮件。" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
        <el-form label-position="top" :model="aiSettings" class="settings-grid settings-grid--two">
          <el-form-item label="AI 搜索方案"><el-switch v-model="aiSettings.enabled" inline-prompt active-text="启用" inactive-text="停用" /></el-form-item>
          <el-form-item label="请求超时（秒）"><el-input-number v-model="aiSettings.requestTimeoutSeconds" :min="10" :max="180" controls-position="right" /></el-form-item>
          <el-form-item label="API 地址"><el-input v-model="aiSettings.baseUrl" placeholder="https://api.openai.com/v1" /></el-form-item>
          <el-form-item label="模型"><el-input v-model="aiSettings.modelName" placeholder="例如 gpt-4o-mini" /></el-form-item>
          <el-form-item label="API Key" class="grid-span-full"><el-input v-model="aiSettings.apiKey" type="password" show-password autocomplete="new-password" placeholder="留空会继续使用已保存的密钥" /></el-form-item>
        </el-form>
      </div>
      <div v-if="canWriteSettings" class="sticky-actions"><span class="muted">保存后，新创建的任务将使用这些设置。</span><el-button type="primary" :loading="rulesSaving" @click="saveRules">保存业务规则与 AI 设置</el-button></div>
    </el-tab-pane>

    <el-tab-pane v-if="canReadProviders" label="Provider" name="providers">
      <div class="panel">
        <div class="panel-heading"><h3>第三方平台密钥</h3><span class="muted">接口协议和字段映射由系统代码维护</span></div>
        <el-alert
          title="配置任务可选择的 Apollo/Hunter API Key"
          description="每个任务在创建时选择 Apollo、Hunter 或同时选择两个；选中的平台分别独立执行公司、联系人和邮箱全流程，不再跨平台自动切换。"
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-table :data="vendorCredentials" stripe v-loading="vendorsLoading">
          <el-table-column prop="display_name" label="Vendor" width="150" />
          <el-table-column label="API Key / 内部 Token" min-width="300">
            <template #default="{ row }"><el-input v-model="vendorKeys[row.vendor]" type="password" show-password :placeholder="row.api_key_configured ? '已配置，留空保存将保留原密钥' : (row.vendor === 'aftership_local' ? '请输入 VERIFIER_TOKEN' : '请输入 API Key')" /></template>
          </el-table-column>
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-switch v-model="row.enabled" inline-prompt active-text="启用" inactive-text="停用" aria-label="启用或停用供应商" /></template></el-table-column>
          <el-table-column label="测试结果" min-width="190">
            <template #default="{ row }"><el-tag v-if="row.last_test_ok === true" type="success">连接成功</el-tag><el-tag v-else-if="row.last_test_ok === false" type="danger">连接失败</el-tag><span v-else class="muted">未测试</span><div v-if="row.last_test_error" class="vendor-error">{{ row.last_test_error }}</div></template>
          </el-table-column>
          <el-table-column label="操作" width="170">
            <template #default="{ row }"><el-button size="small" type="primary" @click="saveVendor(row)">保存</el-button><el-button size="small" :disabled="!row.api_key_configured && !vendorKeys[row.vendor]" @click="testVendor(row)">测试</el-button></template>
          </el-table-column>
        </el-table>
      </div>
      <div class="panel">
        <div class="panel-heading"><div><h3>高级数据源</h3><p class="section-help">添加自建 HTTP 接口或调整数据源优先级。普通使用只需配置上方平台密钥。</p></div><el-button type="primary" plain @click="openProvider()">新增数据源</el-button></div>
        <EntityTable ref="providerTable" endpoint="/provider-configs" :columns="providerColumns">
          <template #cell-type="{ value }"><el-tag type="info">{{ providerTypeLabel(value) }}</el-tag></template>
          <template #cell-enabled="{ row }"><el-switch :model-value="row.enabled" inline-prompt active-text="启用" inactive-text="停用" @change="toggleProvider(row, Boolean($event))" /></template>
          <template #cell-config="{ row }"><span>{{ providerSummary(row) }}</span></template>
          <template #actions="{ row }"><el-button size="small" @click="openProvider(row)">编辑</el-button><el-button size="small" @click="testProvider(row.id)">测试连接</el-button><el-button size="small" type="danger" plain @click="removeProvider(row.id)">删除</el-button></template>
        </EntityTable>
      </div>
    </el-tab-pane>

    <el-tab-pane v-if="canReadUsers || canReadRoles" label="用户与角色" name="access">
      <el-row :gutter="16">
        <el-col v-if="canReadRoles" :xs="24" :lg="canReadUsers ? 12 : 24">
          <div class="panel">
            <div class="panel-heading"><h3>角色与权限</h3><el-button v-if="canWriteRoles" @click="openRole()">新增角色</el-button></div>
            <EntityTable ref="roleTable" endpoint="/roles" :columns="roleColumns">
              <template #cell-permissions="{ value }"><span>{{ permissionSummary(value) }}</span></template>
              <template #actions="{ row }"><el-button v-if="canWriteRoles" size="small" @click="openRole(row)">编辑</el-button></template>
            </EntityTable>
          </div>
        </el-col>
        <el-col v-if="canReadUsers" :xs="24" :lg="canReadRoles ? 12 : 24">
          <div class="panel">
            <div class="panel-heading"><h3>用户</h3><el-button v-if="canWriteUsers" @click="openUser()">新增用户</el-button></div>
            <EntityTable ref="userTable" endpoint="/users" :columns="userColumns">
              <template #cell-status="{ value }"><el-tag :type="value === 'active' ? 'success' : 'info'">{{ value === 'active' ? '启用' : '停用' }}</el-tag></template>
              <template #actions="{ row }"><el-button v-if="canWriteUsers" size="small" @click="openUser(row)">编辑</el-button></template>
            </EntityTable>
          </div>
        </el-col>
      </el-row>
    </el-tab-pane>

    <el-tab-pane v-if="canReadTags || canReadCustomFields" label="字段与标签" name="metadata">
      <el-row :gutter="16">
        <el-col v-if="canReadTags" :xs="24" :lg="canReadCustomFields ? 12 : 24">
          <div class="panel">
            <div class="panel-heading"><h3>标签</h3><el-button v-if="canWriteTags" @click="tagVisible = true">新增标签</el-button></div>
            <EntityTable ref="tagTable" endpoint="/tags" :columns="tagColumns"><template #cell-module="{ value }">{{ moduleLabel(value) }}</template></EntityTable>
          </div>
        </el-col>
        <el-col v-if="canReadCustomFields" :xs="24" :lg="canReadTags ? 12 : 24">
          <div class="panel">
            <div class="panel-heading"><h3>自定义字段</h3><el-button v-if="canWriteCustomFields" @click="customFieldVisible = true">新增字段</el-button></div>
            <EntityTable ref="customFieldTable" endpoint="/custom-fields" :columns="customFieldColumns"><template #cell-module="{ value }">{{ moduleLabel(value) }}</template><template #cell-type="{ value }">{{ fieldTypeLabel(value) }}</template><template #cell-is_required="{ value }"><el-tag :type="value ? 'warning' : 'info'">{{ value ? '必填' : '选填' }}</el-tag></template></EntityTable>
          </div>
        </el-col>
      </el-row>
    </el-tab-pane>

    <el-tab-pane v-if="canReadAudit" label="审计日志" name="audit">
      <div class="panel"><div class="panel-heading"><div><h3>配置变更记录</h3><p class="section-help">用于追溯谁在何时修改了哪些设置，不包含密码和密钥明文。</p></div></div><EntityTable endpoint="/audit-logs" :columns="auditColumns" :page-size="20"><template #cell-action="{ value }">{{ auditActionLabel(value) }}</template><template #cell-entity_type="{ value }">{{ auditEntityLabel(value) }}</template><template #cell-after="{ value }"><span>{{ auditChangeSummary(value) }}</span></template></EntityTable></div>
    </el-tab-pane>
  </el-tabs>

  <el-dialog v-model="providerVisible" :title="provider.id ? '编辑数据源' : '新增数据源'" width="min(760px, 94vw)" destroy-on-close>
    <el-form label-width="130px" :model="provider">
      <el-form-item label="Provider 名称" required><el-input v-model="provider.provider" placeholder="例如 builtin-company-search" /></el-form-item>
      <el-form-item label="Provider 类型"><el-select v-model="provider.type" style="width: 100%"><el-option v-for="item in providerTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item label="适配器">
        <div class="adapter-picker">
          <el-button plain circle aria-label="向左滚动适配器" @click="scrollAdapters(-1)">&lt;</el-button>
          <div ref="adapterScroller" class="adapter-scroller" @wheel.prevent="scrollAdaptersByWheel">
            <el-segmented v-model="provider.adapter" :options="adapterOptions" />
          </div>
          <el-button plain circle aria-label="向右滚动适配器" @click="scrollAdapters(1)">&gt;</el-button>
        </div>
      </el-form-item>
      <template v-if="provider.adapter === 'http'">
        <el-alert v-if="provider.type === 'company_search'" title="搜索引擎/公开目录配置" description="URL 可使用 {{query}}、{{brand_keywords}}、{{categories}}、{{countries}} 占位符。国家请优先映射为 headquarters_country、registered_country 或 origin_country；普通 country 必须声明字段含义，运营国家不能用于品牌归属校验。" type="info" :closable="false" show-icon style="margin-bottom: 18px" />
        <el-form-item label="接口 URL" required><el-input v-model="provider.url" placeholder="https://api.vendor.com/v1/search" /></el-form-item>
        <el-form-item label="HTTP 方法"><el-segmented v-model="provider.method" :options="['POST', 'GET']" /></el-form-item>
        <el-form-item label="API Key"><el-input v-model="provider.api_key" type="password" show-password /></el-form-item>
        <el-form-item label="认证 Header"><el-input v-model="provider.api_key_header" /></el-form-item>
        <el-form-item label="附加 Header JSON"><el-input v-model="provider.headers" type="textarea" :rows="2" placeholder='例如 {"X-Region":"US"}' /></el-form-item>
        <template v-if="provider.type === 'company_search'">
          <el-form-item label="发现来源"><el-select v-model="provider.source_type" style="width: 100%"><el-option label="搜索引擎" value="search_engine" /><el-option label="公开目录" value="public_directory" /><el-option label="商业数据" value="commercial_api" /></el-select></el-form-item>
          <el-form-item label="查询模板"><el-input v-model="provider.query_template" placeholder="{{brand_keywords}} {{categories}} {{countries}}" /></el-form-item>
        </template>
        <el-form-item v-if="provider.type !== 'email_verifier'" label="数据列表路径"><el-input v-model="provider.items_path" placeholder="例如 data.companies" /></el-form-item>
        <el-form-item label="响应根路径"><el-input v-model="provider.response_path" placeholder="可留空" /></el-form-item>
        <el-form-item v-if="provider.type !== 'email_verifier'" label="字段映射"><el-input v-model="provider.field_map" type="textarea" :rows="4" placeholder='{"brand_name":"name","website":"url","headquarters_country":"hq.country"}' /></el-form-item>
        <el-form-item v-if="provider.type === 'company_search'" label="country 字段含义">
          <el-select v-model="provider.country_semantics" style="width: 100%">
            <el-option label="未知/不参与归属校验" value="unknown" />
            <el-option label="品牌总部国家" value="headquarters" />
            <el-option label="企业注册国家" value="registered" />
            <el-option label="品牌起源国家" value="origin" />
            <el-option label="销售/运营国家（不参与归属校验）" value="operating" />
          </el-select>
        </el-form-item>
        <template v-else>
          <el-form-item label="结果字段路径"><el-input v-model="provider.result_path" placeholder="例如 data.result" /></el-form-item>
          <el-form-item label="评分字段路径"><el-input v-model="provider.score_path" placeholder="例如 data.score" /></el-form-item>
        </template>
      </template>
      <template v-else-if="vendorAdapters.includes(provider.adapter)">
        <el-alert :title="vendorHint" type="info" :closable="false" show-icon style="margin-bottom: 18px" />
        <el-alert title="基础配置" description="所有 Vendor 均先填写接口地址、API Key、额度查询接口、额度认证方式和剩余额度字段路径。只有通用 Provider 才需要在下方高级配置中维护请求与响应映射。" type="success" :closable="false" style="margin-bottom: 18px" />
        <el-form-item label="接口地址" required><el-input v-model="provider.endpoint_url" placeholder="请填写该 Provider 的完整 API 地址" /></el-form-item>
        <el-form-item label="API Key" required><el-input v-model="provider.api_key" type="password" show-password /></el-form-item>
        <el-form-item label="额度查询接口" required><el-input v-model="provider.quota_endpoint_url" placeholder="Provider 提供的额度查询 API 地址" /></el-form-item>
        <el-form-item label="额度查询方法"><el-segmented v-model="provider.quota_method" :options="['GET', 'POST']" /></el-form-item>
        <el-form-item label="额度认证 Header"><el-input v-model="provider.quota_api_key_header" placeholder="例如 X-API-KEY 或 Authorization" /></el-form-item>
        <el-form-item label="额度认证前缀"><el-input v-model="provider.quota_api_key_prefix" placeholder="可选，例如 Bearer" /></el-form-item>
        <el-form-item label="额度 API Key 查询参数"><el-input v-model="provider.quota_api_key_query_param" placeholder="可选，例如 api_key；填写后不使用认证 Header" /></el-form-item>
        <el-form-item label="剩余额度字段路径" required><el-input v-model="provider.quota_remaining_path" placeholder="例如 data.requests.searches.available" /></el-form-item>
        <el-form-item label="额度总量字段路径"><el-input v-model="provider.quota_available_path" placeholder="可选；与已用额度字段共同计算剩余额度" /></el-form-item>
        <el-form-item label="额度已用字段路径"><el-input v-model="provider.quota_used_path" placeholder="可选；与额度总量字段共同计算剩余额度" /></el-form-item>
        <el-form-item label="额度恢复时间字段路径"><el-input v-model="provider.quota_reset_at_path" placeholder="可选，例如 data.requests.searches.reset_at" /></el-form-item>
        <el-collapse v-if="catalogAdapters.includes(provider.adapter)" v-model="providerAdvancedSections" class="provider-advanced">
          <el-collapse-item title="高级接口映射" name="request-mapping">
            <el-alert title="仅通用 Provider 需要填写" description="按该 Provider 的官方接口文档配置。可用变量：{{email}}、{{domain}}、{{company_name}}、{{first_name}}、{{last_name}}、{{full_name}}、{{linkedin_url}}、{{titles}}、{{countries}}、{{categories}}、{{brand_keywords}}、{{limit}}。" type="warning" :closable="false" style="margin-bottom: 18px" />
            <el-form-item label="API Key 认证 Header"><el-input v-model="provider.api_key_header" placeholder="与查询参数二选一" /></el-form-item>
            <el-form-item label="API Key 认证前缀"><el-input v-model="provider.api_key_prefix" placeholder="可选，例如 Bearer" /></el-form-item>
            <el-form-item label="API Key 查询参数"><el-input v-model="provider.api_key_query_param" placeholder="与认证 Header 二选一，例如 api_key" /></el-form-item>
            <el-form-item label="请求方法"><el-segmented v-model="provider.request_method" :options="['GET', 'POST']" /></el-form-item>
            <el-form-item label="请求 Header JSON"><el-input v-model="provider.request_headers" type="textarea" :rows="2" placeholder='例如 {"X-Region":"US"}' /></el-form-item>
            <el-form-item label="请求查询参数 JSON"><el-input v-model="provider.request_query" type="textarea" :rows="3" placeholder='例如 {"email":"{{email}}","limit":"{{limit}}"}' /></el-form-item>
            <el-form-item label="请求 Body JSON"><el-input v-model="provider.request_body" type="textarea" :rows="4" placeholder='POST 示例：{"domain":"{{domain}}","name":"{{company_name}}"}' /></el-form-item>
            <el-form-item label="异步结果地址路径"><el-input v-model="provider.poll_result_url_path" placeholder="可选，例如 data.result_url" /></el-form-item>
            <template v-if="provider.type === 'email_verifier'">
              <el-form-item label="验证结果路径" required><el-input v-model="provider.result_path" placeholder="例如 data.state" /></el-form-item>
              <el-form-item label="验证评分路径"><el-input v-model="provider.score_path" placeholder="可选，例如 data.score" /></el-form-item>
              <el-form-item label="结果状态映射 JSON"><el-input v-model="provider.result_map" type="textarea" :rows="3" placeholder='例如 {"deliverable":"valid","undeliverable":"invalid"}' /></el-form-item>
            </template>
            <template v-else>
              <el-form-item label="响应列表路径" required><el-input v-model="provider.response_items_path" placeholder="例如 data.items" /></el-form-item>
              <el-form-item label="响应字段映射 JSON" required><el-input v-model="provider.response_field_map" type="textarea" :rows="5" placeholder='例如 {"brand_name":"name","website":"website","domain":"domain"}' /></el-form-item>
            </template>
            <el-form-item label="额度附加 Header JSON"><el-input v-model="provider.quota_headers" type="textarea" :rows="2" placeholder='例如 {"X-Plan":"production"}' /></el-form-item>
            <el-form-item label="额度请求 Body JSON"><el-input v-model="provider.quota_request_body" type="textarea" :rows="3" placeholder='POST 查询额度时填写，例如 {"workspace":"default"}' /></el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item v-if="provider.adapter === 'hunter' && provider.type === 'company_search'" label="Domain Finder 接口"><el-input v-model="provider.domain_finder_endpoint_url" placeholder="可选：用于补齐缺失的公司域名" /></el-form-item>
        <el-form-item v-if="provider.adapter === 'hunter' && provider.type === 'company_search' && provider.domain_finder_endpoint_url" label="Domain Finder 测试公司"><el-input v-model="provider.test_domain_finder_company" placeholder="例如 Acme Corporation" /></el-form-item>
        <el-form-item v-if="provider.adapter === 'hunter' && provider.type === 'brand_email_search'" label="Email Count 接口"><el-input v-model="provider.email_count_endpoint_url" placeholder="可选：域名邮箱搜索前先判断是否存在邮箱" /></el-form-item>
        <template v-if="provider.adapter === 'apollo' && provider.type === 'contact_search'">
          <el-form-item label="批量联系人富化接口"><el-input v-model="provider.bulk_enrichment_endpoint_url" placeholder="可选：补充联系人邮箱和资料" /></el-form-item>
          <el-form-item v-if="provider.bulk_enrichment_endpoint_url" label="单批富化数量"><el-input-number v-model="provider.bulk_enrichment_batch_size" :min="1" :max="10" /></el-form-item>
          <el-form-item v-if="provider.bulk_enrichment_endpoint_url" label="富化个人邮箱"><el-switch v-model="provider.bulk_enrichment_reveal_personal_emails" /></el-form-item>
          <el-form-item v-if="provider.bulk_enrichment_endpoint_url" label="富化电话号码"><el-switch v-model="provider.bulk_enrichment_reveal_phone_number" /></el-form-item>
          <el-form-item v-if="provider.bulk_enrichment_endpoint_url" label="测试批量富化"><el-switch v-model="provider.test_bulk_enrichment" /></el-form-item>
        </template>
        <el-form-item v-if="provider.adapter === 'apollo' && provider.type === 'company_search'" label="测试企业关键词"><el-input v-model="provider.test_query" placeholder="例如 Acme" /></el-form-item>
        <el-form-item v-if="provider.type === 'brand_email_search'" label="测试品牌域名"><el-input v-model="provider.test_domain" placeholder="例如 mango.com" /></el-form-item>
        <el-form-item v-if="provider.type === 'email_verifier'" label="测试邮箱"><el-input v-model="provider.test_email" placeholder="例如 test@yourcompany.com" /></el-form-item>
        <el-form-item label="单次返回上限"><el-input-number v-model="provider.limit" :min="1" :max="100" /></el-form-item>
      </template>
      <template v-else>
        <el-form-item v-if="provider.type === 'company_search' || provider.type === 'email_finder' || provider.type === 'brand_email_search'" label="域名后缀"><el-input v-model="provider.default_domain_suffix" placeholder="com" /></el-form-item>
        <el-form-item v-if="provider.type === 'company_search'" label="品牌关键词"><el-input v-model="provider.brand_keywords" type="textarea" :rows="3" placeholder="每行一个品牌关键词" /></el-form-item>
        <el-form-item v-if="provider.type === 'company_search'" label="国家/地区"><el-input v-model="provider.countries" type="textarea" :rows="2" placeholder="每行一个国家或地区" /></el-form-item>
        <el-form-item v-if="provider.type === 'company_search'" label="品类"><el-input v-model="provider.categories" type="textarea" :rows="2" placeholder="每行一个品类" /></el-form-item>
        <el-form-item v-if="provider.type === 'company_search'" label="品牌种子 JSON"><el-input v-model="provider.companies_json" type="textarea" :rows="5" placeholder='[{"brand_name":"Acme","website":"https://acme.com"}]' /></el-form-item>
        <el-form-item v-if="provider.type === 'contact_search'" label="目标职位"><el-input v-model="provider.titles" type="textarea" :rows="3" placeholder="每行一个职位" /></el-form-item>
        <el-form-item v-if="provider.type === 'contact_search'" label="联系人 JSON"><el-input v-model="provider.contacts_json" type="textarea" :rows="5" placeholder='[{"first_name":"Jane","last_name":"Chen","title":"Head of Buying"}]' /></el-form-item>
        <el-form-item v-if="provider.type === 'email_finder' || provider.type === 'brand_email_search'" label="邮箱 JSON"><el-input v-model="provider.emails_json" type="textarea" :rows="5" placeholder='["sales@example.com"]' /></el-form-item>
        <el-form-item v-if="provider.type === 'email_finder' || provider.type === 'brand_email_search'" label="最低置信度"><el-input-number v-model="provider.min_confidence" :min="0" :max="100" /></el-form-item>
        <el-form-item v-if="provider.type === 'email_finder' || provider.type === 'brand_email_search' || provider.type === 'contact_search'" label="返回上限"><el-input-number v-model="provider.limit" :min="1" :max="50" /></el-form-item>
      </template>
      <el-form-item label="优先级"><el-input-number v-model="provider.priority" :min="0" /></el-form-item>
      <el-form-item label="立即启用"><el-switch v-model="provider.enabled" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="providerVisible = false">取消</el-button><el-button type="primary" :loading="providerSaving" @click="saveProvider">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="roleVisible" :title="roleForm.id ? '编辑角色' : '新增角色'" width="min(680px, 94vw)">
    <el-form label-position="top"><el-form-item label="角色名称"><el-input v-model="roleForm.name" :disabled="Boolean(roleForm.id)" placeholder="例如：销售主管" /></el-form-item><el-form-item label="可使用的功能"><div class="permission-list"><label v-for="item in permissionOptions" :key="item.resource" class="permission-row"><span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span><el-checkbox-group v-model="roleForm.permissions[item.resource]"><el-checkbox v-for="action in item.actions" :key="action.value" :label="action.value">{{ action.label }}</el-checkbox></el-checkbox-group></label></div></el-form-item></el-form>
    <template #footer><el-button @click="roleVisible = false">取消</el-button><el-button type="primary" @click="saveRole">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="userVisible" :title="userForm.id ? '编辑用户' : '新增用户'" width="520px">
    <el-form label-width="110px"><el-form-item label="姓名"><el-input v-model="userForm.name" /></el-form-item><el-form-item label="邮箱"><el-input v-model="userForm.email" :disabled="Boolean(userForm.id)" /></el-form-item><el-form-item :label="userForm.id ? '重置密码' : '密码'"><el-input v-model="userForm.password" type="password" show-password /></el-form-item><el-form-item label="角色"><el-select v-model="userForm.role_id" clearable style="width: 100%"><el-option v-for="role in roles" :key="role.id" :label="role.name" :value="role.id" /></el-select></el-form-item><el-form-item label="状态"><el-select v-model="userForm.status" style="width: 100%"><el-option label="启用" value="active" /><el-option label="停用" value="disabled" /></el-select></el-form-item></el-form>
    <template #footer><el-button @click="userVisible = false">取消</el-button><el-button type="primary" @click="saveUser">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="tagVisible" title="新增标签" width="420px"><el-form label-width="90px"><el-form-item label="名称"><el-input v-model="tagForm.name" /></el-form-item><el-form-item label="对象"><el-select v-model="tagForm.module" style="width: 100%"><el-option v-for="item in moduleOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-form><template #footer><el-button @click="tagVisible = false">取消</el-button><el-button type="primary" @click="saveTag">保存</el-button></template></el-dialog>

  <el-dialog v-model="customFieldVisible" title="新增自定义字段" width="min(480px, 94vw)"><el-form label-position="top"><el-form-item label="字段名称"><el-input v-model="customFieldForm.name" placeholder="例如：客户等级" /></el-form-item><el-form-item label="用于"><el-select v-model="customFieldForm.module" style="width: 100%"><el-option v-for="item in moduleOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item><el-form-item label="填写方式"><el-select v-model="customFieldForm.type" style="width: 100%"><el-option v-for="item in fieldTypes" :key="item" :label="fieldTypeLabel(item)" :value="item" /></el-select></el-form-item><el-form-item label="使用规则"><el-checkbox v-model="customFieldForm.is_required">必须填写</el-checkbox><el-checkbox v-model="customFieldForm.is_searchable">可用于筛选</el-checkbox><el-checkbox v-model="customFieldForm.show_in_list">在列表中显示</el-checkbox></el-form-item></el-form><template #footer><el-button @click="customFieldVisible = false">取消</el-button><el-button type="primary" @click="saveCustomField">创建字段</el-button></template></el-dialog>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const canReadSettings = computed(() => auth.hasPermission('settings:read'))
const canWriteSettings = computed(() => auth.hasPermission('settings:write'))
const canReadProviders = computed(() => auth.hasPermission('providers:read'))
const canReadRoles = computed(() => auth.hasPermission('roles:read'))
const canWriteRoles = computed(() => auth.hasPermission('roles:write'))
const canReadUsers = computed(() => auth.hasPermission('users:read'))
const canWriteUsers = computed(() => auth.hasPermission('users:write'))
const canReadTags = computed(() => auth.hasPermission('tags:read'))
const canWriteTags = computed(() => auth.hasPermission('tags:write'))
const canReadCustomFields = computed(() => auth.hasPermission('custom_fields:read'))
const canWriteCustomFields = computed(() => auth.hasPermission('custom_fields:write'))
const canReadAudit = computed(() => auth.hasPermission('audit:read'))
const firstAllowedTab = () => canReadSettings.value ? 'rules' : canReadProviders.value ? 'providers' : (canReadUsers.value || canReadRoles.value) ? 'access' : (canReadTags.value || canReadCustomFields.value) ? 'metadata' : 'audit'
const activeTab = ref(firstAllowedTab())
const providerTable = ref<InstanceType<typeof EntityTable>>()
const roleTable = ref<InstanceType<typeof EntityTable>>()
const userTable = ref<InstanceType<typeof EntityTable>>()
const tagTable = ref<InstanceType<typeof EntityTable>>()
const customFieldTable = ref<InstanceType<typeof EntityTable>>()
const adapterScroller = ref<HTMLElement>()
const providerAdvancedSections = ref<string[]>([])
const providerVisible = ref(false)
const providerSaving = ref(false)
const rulesSaving = ref(false)
const pageLoading = ref(false)
const roleVisible = ref(false)
const userVisible = ref(false)
const tagVisible = ref(false)
const customFieldVisible = ref(false)
const roles = ref<Array<{ id: string; name: string }>>([])
const capabilityConfigs = ref<Array<{ name: string; provider: string; endpoint: string; status: string; parent: any }>>([])
type VendorCredential = { vendor: string; display_name: string; enabled: boolean; api_key_configured: boolean; last_test_ok: boolean | null; last_test_error: string | null }
const vendorCredentials = ref<VendorCredential[]>([])
const vendorKeys = reactive<Record<string, string>>({})
const vendorsLoading = ref(false)
const systemRules = reactive({ p1: '', p2: '', p3: '', excluded: '', validScore: 70, riskyScore: 40, maxAttempts: 3, retryDelay: 60, maxConcurrency: 4, defaultContactLimit: 5 })
const aiSettings = reactive({ enabled: false, baseUrl: 'https://api.openai.com/v1', modelName: 'gpt-4o-mini', requestTimeoutSeconds: 60, apiKey: '' })
const roleForm = reactive<{ id: string; name: string; permissions: Record<string, string[]> }>({ id: '', name: '', permissions: {} })
const userForm = reactive({ id: '', name: '', email: '', password: '', role_id: '', status: 'active' })
const tagForm = reactive({ name: '', module: 'brands' })
const customFieldForm = reactive({ name: '', module: 'brands', type: 'text', is_required: false, is_searchable: true, show_in_list: true })

const moduleOptions = [{ label: '品牌', value: 'brands' }, { label: '联系人', value: 'contacts' }, { label: '邮箱', value: 'emails' }]
const fieldTypes = ['text', 'number', 'date', 'single_select', 'multi_select', 'boolean', 'url', 'email', 'phone']
const fieldTypeLabels: Record<string, string> = { text: '文本', number: '数字', date: '日期', single_select: '单选', multi_select: '多选', boolean: '是/否', url: '网址', email: '邮箱', phone: '电话' }
const permissionOptions = [
  { resource: 'brands', label: '品牌', description: '查看、编辑和导出品牌资料', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }, { label: '导出', value: 'export' }] },
  { resource: 'contacts', label: '联系人', description: '查看、编辑和导出联系人', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }, { label: '导出', value: 'export' }] },
  { resource: 'emails', label: '邮箱', description: '管理、验证和导出邮箱', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }, { label: '验证', value: 'verify' }, { label: '导出', value: 'export' }] },
  { resource: 'tasks', label: '任务', description: '创建、修改和运行搜索任务', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }, { label: '运行', value: 'execute' }] },
  { resource: 'providers', label: '数据服务', description: '管理第三方数据源和密钥', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'settings', label: '系统设置', description: '查看或修改全局业务规则', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'users', label: '用户', description: '管理系统登录用户', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'roles', label: '角色', description: '管理角色及其权限', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'audit', label: '审计日志', description: '查看系统操作记录', actions: [{ label: '查看', value: 'read' }] },
  { resource: 'tags', label: '标签', description: '管理品牌、联系人和邮箱标签', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'custom_fields', label: '自定义字段', description: '管理业务对象的扩展字段', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
  { resource: 'import', label: '导入', description: '导入业务数据', actions: [{ label: '执行', value: 'execute' }] },
  { resource: 'export', label: '统一导出', description: '执行系统级数据导出', actions: [{ label: '执行', value: 'execute' }] },
  { resource: 'dedup', label: '数据去重', description: '执行重复数据清理', actions: [{ label: '执行', value: 'execute' }] },
  { resource: 'blacklist', label: '黑名单', description: '查看和维护黑名单', actions: [{ label: '查看', value: 'read' }, { label: '编辑', value: 'write' }] },
]
const adapterOptions = [{ label: 'HTTP', value: 'http' }, { label: '内置', value: 'builtin' }]
const providerTypes = [{ label: '企业/品牌搜索', value: 'company_search' }, { label: '联系人搜索', value: 'contact_search' }, { label: '品牌域名邮箱搜索', value: 'brand_email_search' }, { label: '联系人邮箱查找', value: 'email_finder' }, { label: '邮箱验证', value: 'email_verifier' }, { label: '通知', value: 'notification' }]
const providerColumns: TableColumn[] = [{ key: 'provider', label: '名称', width: 170 }, { key: 'type', label: '类型', width: 150 }, { key: 'enabled', label: '启用', width: 90 }, { key: 'priority', label: '优先级', width: 90 }, { key: 'config', label: '连接配置', width: 360 }]
const roleColumns: TableColumn[] = [{ key: 'name', label: '角色', width: 120 }, { key: 'permissions', label: '权限', width: 360 }]
const userColumns: TableColumn[] = [{ key: 'name', label: '姓名', width: 110 }, { key: 'email', label: '邮箱', width: 220 }, { key: 'role_name', label: '角色', width: 110 }, { key: 'status', label: '状态', width: 90 }]
const tagColumns: TableColumn[] = [{ key: 'name', label: '标签', width: 180 }, { key: 'module', label: '对象', width: 120 }, { key: 'created_at', label: '创建时间', width: 180 }]
const customFieldColumns: TableColumn[] = [{ key: 'name', label: '字段', width: 160 }, { key: 'module', label: '对象', width: 110 }, { key: 'type', label: '类型', width: 140 }, { key: 'is_required', label: '必填', width: 80 }]
const auditColumns: TableColumn[] = [{ key: 'action', label: '操作', width: 180 }, { key: 'entity_type', label: '对象类型', width: 120 }, { key: 'entity_id', label: '对象 ID', width: 260 }, { key: 'after', label: '变更内容', width: 300 }, { key: 'created_at', label: '时间', width: 180 }]

const catalogAdapters = ['snov', 'prospeo', 'pdl', 'dropcontact', 'lusha', 'neverbounce', 'emailable', 'wappalyzer', 'builtwith', 'crunchbase']
const vendorAdapters = ['apollo', 'hunter', 'zerobounce', ...catalogAdapters]
const vendorHint = computed(() => ({
  apollo: 'Apollo：支持企业/品牌搜索和联系人搜索。',
  hunter: 'Hunter：品牌域名邮箱搜索使用 Domain Search；联系人邮箱查找使用 Email Finder；同时支持邮箱验证。',
  zerobounce: 'ZeroBounce：仅支持邮箱验证。',
  snov: 'Snov.io：企业、联系人、邮箱查找和邮箱验证均可通过官方接口映射配置。',
  prospeo: 'Prospeo：支持企业、联系人和邮箱查找；按官方接口填写请求和响应映射。',
  pdl: 'People Data Labs：支持企业与联系人富化/搜索。',
  dropcontact: 'Dropcontact：支持联系人/域名邮箱查找和邮箱验证。',
  lusha: 'Lusha：支持企业与联系人搜索或富化。',
  neverbounce: 'NeverBounce：仅支持邮箱验证。',
  emailable: 'Emailable：仅支持邮箱验证。',
  wappalyzer: 'Wappalyzer：支持基于域名的企业技术栈查询。',
  builtwith: 'BuiltWith：支持基于域名的企业技术栈查询。',
  crunchbase: 'Crunchbase：支持企业/品牌资料查询。',
}[provider.adapter] || ''))
adapterOptions.push(
  { label: 'Apollo', value: 'apollo' }, { label: 'Hunter', value: 'hunter' }, { label: 'ZeroBounce', value: 'zerobounce' },
  { label: 'Snov.io', value: 'snov' }, { label: 'Prospeo', value: 'prospeo' }, { label: 'People Data Labs', value: 'pdl' },
  { label: 'Dropcontact', value: 'dropcontact' }, { label: 'Lusha', value: 'lusha' }, { label: 'NeverBounce', value: 'neverbounce' },
  { label: 'Emailable', value: 'emailable' }, { label: 'Wappalyzer', value: 'wappalyzer' }, { label: 'BuiltWith', value: 'builtwith' }, { label: 'Crunchbase', value: 'crunchbase' },
)

function defaultProvider() {
  return { id: '', provider: '', type: 'company_search', adapter: 'http', url: '', endpoint_url: '', quota_endpoint_url: '', quota_method: 'GET', quota_api_key_header: '', quota_api_key_prefix: '', quota_api_key_query_param: '', quota_headers: '{}', quota_request_body: '{}', quota_remaining_path: '', quota_available_path: '', quota_used_path: '', quota_reset_at_path: '', domain_finder_endpoint_url: '', email_count_endpoint_url: '', bulk_enrichment_endpoint_url: '', bulk_enrichment_batch_size: 10, bulk_enrichment_reveal_personal_emails: false, bulk_enrichment_reveal_phone_number: false, test_bulk_enrichment: false, test_domain_finder_company: '', method: 'POST', api_key: '', api_key_header: 'Authorization', api_key_prefix: '', api_key_query_param: '', request_method: 'GET', request_headers: '{}', request_query: '{}', request_body: '{}', response_items_path: '', response_field_map: '{}', poll_result_url_path: '', result_map: '{}', headers: '', source_type: 'search_engine', query_template: '{{brand_keywords}} {{categories}} {{countries}}', items_path: 'companies', response_path: '', field_map: '', country_semantics: 'unknown', result_path: 'result', score_path: 'score', default_domain_suffix: 'com', brand_keywords: '', countries: '', categories: '', companies_json: '', titles: 'Head of Buying', contacts_json: '', emails_json: '', min_confidence: 40, limit: 5, test_query: '', test_domain: '', test_email: '', priority: 100, enabled: false }
}
const provider = reactive(defaultProvider())

function openProvider(row?: any) {
  Object.assign(provider, defaultProvider())
  if (row) {
    const config = row.config || {}
    Object.assign(provider, {
      id: row.id,
      provider: row.provider,
      type: row.type,
      priority: row.priority,
      enabled: row.enabled,
      adapter: config.adapter || 'http',
      url: config.url || '',
      endpoint_url: config.endpoint_url || '',
      method: config.method || 'POST',
      api_key: '',
      api_key_header: config.api_key_header || 'Authorization',
      api_key_prefix: config.api_key_prefix || '',
      api_key_query_param: config.api_key_query_param || '',
      request_method: config.request_method || 'GET',
      request_headers: JSON.stringify(config.request_headers || {}, null, 2),
      request_query: JSON.stringify(config.request_query || {}, null, 2),
      request_body: JSON.stringify(config.request_body || {}, null, 2),
      response_items_path: config.response_items_path || '',
      response_field_map: JSON.stringify(config.response_field_map || {}, null, 2),
      poll_result_url_path: config.poll_result_url_path || '',
      result_map: JSON.stringify(config.result_map || {}, null, 2),
      headers: config.headers ? JSON.stringify(config.headers, null, 2) : '',
      source_type: config.source_type || 'search_engine',
      query_template: config.query_template || '{{brand_keywords}} {{categories}} {{countries}}',
      items_path: config.items_path || 'companies',
      response_path: config.response_path || '',
      field_map: config.field_map ? JSON.stringify(config.field_map, null, 2) : '',
      country_semantics: config.country_semantics || 'unknown',
      result_path: config.result_path || 'result',
      score_path: config.score_path || 'score',
      default_domain_suffix: config.default_domain_suffix || 'com',
      brand_keywords: linesToText(config.brand_keywords),
      countries: linesToText(config.countries),
      categories: linesToText(config.categories),
      companies_json: config.companies ? JSON.stringify(config.companies, null, 2) : '',
      titles: linesToText(config.titles) || 'Head of Buying',
      contacts_json: config.contacts ? JSON.stringify(config.contacts, null, 2) : '',
      emails_json: config.emails ? JSON.stringify(config.emails, null, 2) : '',
      min_confidence: config.min_confidence ?? 40,
      limit: config.limit ?? 5,
      test_query: config.test_query || '',
      test_domain: config.test_domain || '',
      test_email: config.test_email || '',
      quota_endpoint_url: config.quota_endpoint_url || '',
      quota_method: config.quota_method || 'GET',
      quota_api_key_header: config.quota_api_key_header || '',
      quota_api_key_prefix: config.quota_api_key_prefix || '',
      quota_api_key_query_param: config.quota_api_key_query_param || '',
      quota_headers: JSON.stringify(config.quota_headers || {}, null, 2),
      quota_request_body: JSON.stringify(config.quota_request_body || {}, null, 2),
      quota_remaining_path: config.quota_remaining_path || '',
      quota_available_path: config.quota_available_path || '',
      quota_used_path: config.quota_used_path || '',
      quota_reset_at_path: config.quota_reset_at_path || '',
      domain_finder_endpoint_url: config.domain_finder_endpoint_url || '',
      email_count_endpoint_url: config.email_count_endpoint_url || '',
      bulk_enrichment_endpoint_url: config.bulk_enrichment_endpoint_url || '',
      bulk_enrichment_batch_size: config.bulk_enrichment_batch_size ?? 10,
      bulk_enrichment_reveal_personal_emails: config.bulk_enrichment_reveal_personal_emails ?? false,
      bulk_enrichment_reveal_phone_number: config.bulk_enrichment_reveal_phone_number ?? false,
      test_bulk_enrichment: config.test_bulk_enrichment ?? false,
      test_domain_finder_company: config.test_domain_finder_company || '',
    })
  }
  providerAdvancedSections.value = catalogAdapters.includes(provider.adapter) ? ['request-mapping'] : []
  providerVisible.value = true
}

watch(() => [provider.adapter, provider.type], ([adapter, type]) => {
  const supported = ({
    apollo: ['company_search', 'contact_search'], hunter: ['company_search', 'brand_email_search', 'email_finder', 'email_verifier'], zerobounce: ['email_verifier'],
    snov: ['company_search', 'contact_search', 'email_finder', 'brand_email_search', 'email_verifier'], prospeo: ['company_search', 'contact_search', 'email_finder'],
    pdl: ['company_search', 'contact_search'], dropcontact: ['email_finder', 'brand_email_search', 'email_verifier'], lusha: ['company_search', 'contact_search'],
    neverbounce: ['email_verifier'], emailable: ['email_verifier'], wappalyzer: ['company_search'], builtwith: ['company_search'], crunchbase: ['company_search'],
  } as Record<string, string[]>)[adapter]
  if (supported && !supported.includes(type)) provider.type = supported[0]
})

watch(() => provider.adapter, (adapter, previousAdapter) => {
  providerAdvancedSections.value = catalogAdapters.includes(adapter) ? ['request-mapping'] : []
  if (!provider.id && previousAdapter === 'http' && catalogAdapters.includes(adapter) && provider.api_key_header === 'Authorization') {
    provider.api_key_header = ''
  }
})

async function loadRules() {
  try {
    const { data } = await api.get('/system-settings')
    const titles = data.title_dictionary || {}
    const emails = data.email_rules || {}
    const tasks = data.task_rules || {}
    systemRules.p1 = linesToText(titles.p1)
    systemRules.p2 = linesToText(titles.p2)
    systemRules.p3 = linesToText(titles.p3)
    systemRules.excluded = linesToText(titles.excluded)
    systemRules.validScore = emails.valid_score ?? 70
    systemRules.riskyScore = emails.risky_score ?? 40
    systemRules.maxAttempts = tasks.max_attempts ?? 3
    systemRules.retryDelay = tasks.retry_delay_seconds ?? 60
    systemRules.maxConcurrency = tasks.max_concurrency ?? 4
    systemRules.defaultContactLimit = tasks.default_contact_limit ?? 5
    const ai = data.ai || {}
    aiSettings.enabled = ai.enabled ?? false
    aiSettings.baseUrl = ai.base_url || 'https://api.openai.com/v1'
    aiSettings.modelName = ai.model_name || 'gpt-4o-mini'
    aiSettings.requestTimeoutSeconds = ai.request_timeout_seconds ?? 60
    aiSettings.apiKey = ai.api_key || ''
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function reloadCurrentTab() {
  pageLoading.value = true
  try {
    if (activeTab.value === 'rules') await loadRules()
    else if (activeTab.value === 'providers') await Promise.all([loadVendorSettings(), providerTable.value?.load()])
    else if (activeTab.value === 'access') await Promise.all([canReadRoles.value ? loadRoles() : Promise.resolve(), canReadRoles.value ? roleTable.value?.load() : Promise.resolve(), canReadUsers.value ? userTable.value?.load() : Promise.resolve()])
    else if (activeTab.value === 'metadata') await Promise.all([canReadTags.value ? tagTable.value?.load() : Promise.resolve(), canReadCustomFields.value ? customFieldTable.value?.load() : Promise.resolve()])
    ElMessage.success('已刷新')
  } finally { pageLoading.value = false }
}

watch(activeTab, () => { void reloadCurrentTab() })

async function loadVendorSettings() {
  vendorsLoading.value = true
  try {
    const credentials = await api.get('/vendor-credentials')
    vendorCredentials.value = credentials.data || []
    for (const item of vendorCredentials.value) vendorKeys[item.vendor] = ''
  } catch (error) { ElMessage.error((error as Error).message) } finally { vendorsLoading.value = false }
}

async function saveVendor(row: VendorCredential, quiet = false) {
  try {
    const payload: Record<string, unknown> = { enabled: row.enabled }
    if (vendorKeys[row.vendor]?.trim()) payload.api_key = vendorKeys[row.vendor].trim()
    await api.patch(`/vendor-credentials/${row.vendor}`, payload)
    vendorKeys[row.vendor] = ''
    await loadVendorSettings()
    if (!quiet) ElMessage.success(`${row.display_name} 配置已保存`)
  } catch (error) {
    if (quiet) throw error
    ElMessage.error((error as Error).message)
  }
}

async function testVendor(row: VendorCredential) {
  try {
    if (vendorKeys[row.vendor]?.trim()) await saveVendor(row, true)
    const { data } = await api.post(`/vendor-credentials/${row.vendor}/test`)
    await loadVendorSettings()
    data.ok ? ElMessage.success(`${row.display_name} 连接成功`) : ElMessage.error(data.error_message || `${row.display_name} 连接失败`)
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function saveRules() {
  if (systemRules.riskyScore > systemRules.validScore) return ElMessage.warning('风险邮箱分数不能高于有效邮箱分数')
  if (aiSettings.enabled && (!aiSettings.baseUrl.trim() || !aiSettings.modelName.trim())) return ElMessage.warning('启用 AI 前请填写 API 地址和模型')
  rulesSaving.value = true
  try {
    await api.patch('/system-settings', { title_dictionary: { p1: lines(systemRules.p1), p2: lines(systemRules.p2), p3: lines(systemRules.p3), excluded: lines(systemRules.excluded) }, email_rules: { valid_score: systemRules.validScore, risky_score: systemRules.riskyScore }, task_rules: { max_attempts: systemRules.maxAttempts, retry_delay_seconds: systemRules.retryDelay, max_concurrency: systemRules.maxConcurrency, default_contact_limit: systemRules.defaultContactLimit }, ai: { enabled: aiSettings.enabled, provider: 'openai_compatible', base_url: aiSettings.baseUrl, model_name: aiSettings.modelName, request_timeout_seconds: aiSettings.requestTimeoutSeconds, api_key: aiSettings.apiKey } })
    ElMessage.success('业务规则已保存')
  } catch (error) { ElMessage.error((error as Error).message) } finally { rulesSaving.value = false }
}

async function loadRoles() {
  try { roles.value = (await api.get('/roles', { params: { page_size: 100 } })).data.items || [] } catch (error) { ElMessage.error((error as Error).message) }
}

function openRole(row?: any) {
  Object.assign(roleForm, row ? { id: row.id, name: row.name, permissions: normalizePermissions(row.permissions) } : { id: '', name: '', permissions: { brands: ['read'], contacts: ['read'], emails: ['read'], tasks: ['read'] } })
  roleVisible.value = true
}

async function saveRole() {
  if (!roleForm.name.trim()) return ElMessage.warning('请填写角色名称')
  try {
    const permissions = Object.fromEntries(Object.entries(roleForm.permissions).filter(([, actions]) => actions.length))
    if (roleForm.id) await api.patch(`/roles/${roleForm.id}`, { permissions })
    else await api.post('/roles', { name: roleForm.name, permissions })
    roleVisible.value = false
    await Promise.all([roleTable.value?.load(), loadRoles()])
    ElMessage.success('角色已保存')
  } catch (error) { ElMessage.error((error as Error).message) }
}

function openUser(row?: any) {
  Object.assign(userForm, row ? { id: row.id, name: row.name, email: row.email, password: '', role_id: row.role_id || '', status: row.status } : { id: '', name: '', email: '', password: '', role_id: '', status: 'active' })
  userVisible.value = true
}

async function saveUser() {
  if (!userForm.name.trim() || (!userForm.id && (!userForm.email.trim() || userForm.password.length < 8))) return ElMessage.warning('请填写姓名、邮箱和至少 8 位密码')
  try {
    if (userForm.id) {
      const payload: Record<string, unknown> = { name: userForm.name, role_id: userForm.role_id || null, status: userForm.status }
      if (userForm.password) payload.password = userForm.password
      await api.patch(`/users/${userForm.id}`, payload)
    } else await api.post('/users', {
      name: userForm.name.trim(),
      email: userForm.email.trim(),
      password: userForm.password,
      role_id: userForm.role_id || null,
      status: userForm.status,
    })
    userVisible.value = false
    await userTable.value?.load()
    ElMessage.success('用户已保存')
  } catch (error) { ElMessage.error((error as Error).message) }
}

async function saveTag() {
  if (!tagForm.name.trim()) return ElMessage.warning('请填写标签名称')
  try { await api.post('/tags', tagForm); tagVisible.value = false; Object.assign(tagForm, { name: '', module: 'brands' }); await tagTable.value?.load(); ElMessage.success('标签已保存') } catch (error) { ElMessage.error((error as Error).message) }
}

async function saveCustomField() {
  if (!customFieldForm.name.trim()) return ElMessage.warning('请填写字段名称')
  try { await api.post('/custom-fields', customFieldForm); customFieldVisible.value = false; Object.assign(customFieldForm, { name: '', module: 'brands', type: 'text', is_required: false, is_searchable: true, show_in_list: true }); await customFieldTable.value?.load(); ElMessage.success('自定义字段已保存') } catch (error) { ElMessage.error((error as Error).message) }
}

async function saveProvider() {
  if (!provider.provider.trim()) return ElMessage.warning('请填写 Provider 名称')
  if (provider.adapter === 'http' && !provider.url.trim()) return ElMessage.warning('请填写接口 URL')
  if (vendorAdapters.includes(provider.adapter) && !provider.endpoint_url.trim()) return ElMessage.warning('请填写 Provider 接口地址')
  if (vendorAdapters.includes(provider.adapter) && (!provider.quota_endpoint_url.trim() || !provider.quota_remaining_path.trim())) return ElMessage.warning('请填写额度查询接口和剩余额度字段路径')
  if (catalogAdapters.includes(provider.adapter) && !provider.api_key_header.trim() && !provider.api_key_query_param.trim()) return ElMessage.warning('请填写 API Key 认证 Header 或查询参数')
  if (catalogAdapters.includes(provider.adapter) && !provider.quota_api_key_header.trim() && !provider.quota_api_key_query_param.trim()) return ElMessage.warning('请填写额度 API Key 认证 Header 或查询参数')
  if (catalogAdapters.includes(provider.adapter) && provider.type === 'email_verifier' && !provider.result_path.trim()) return ElMessage.warning('请填写验证结果路径')
  if (catalogAdapters.includes(provider.adapter) && provider.type !== 'email_verifier' && (!provider.response_items_path.trim() || !provider.response_field_map.trim())) return ElMessage.warning('请填写响应列表路径和字段映射')
  if (provider.adapter === 'builtin' && provider.type === 'notification') return ElMessage.warning('通知 Provider 请选择 HTTP 适配器')
  if (!provider.id && vendorAdapters.includes(provider.adapter) && !provider.api_key.trim()) return ElMessage.warning('请填写 API Key')
  providerSaving.value = true
  try {
    const payload = { provider: provider.provider, type: provider.type, priority: provider.priority, enabled: provider.enabled, config: provider.adapter === 'builtin' ? builtinConfig() : provider.adapter === 'http' ? httpConfig() : vendorConfig() }
    if (provider.id) await api.patch(`/provider-configs/${provider.id}`, payload)
    else await api.post('/provider-configs', payload)
    providerVisible.value = false
    Object.assign(provider, defaultProvider())
    await Promise.all([providerTable.value?.load(), loadCapabilityConfigs()])
    ElMessage.success('Provider 已保存')
  } catch (error) { ElMessage.error((error as Error).message) } finally { providerSaving.value = false }
}

async function loadCapabilityConfigs() {
  try {
    const { data } = await api.get('/provider-configs', { params: { page: 1, page_size: 100 } })
    capabilityConfigs.value = (data.items || []).flatMap((row: any) => {
      const config = row.config || {}
      const status = row.enabled ? '已启用' : '已停用'
      return [
        config.domain_finder_endpoint_url ? { name: 'Hunter Domain Finder', provider: row.provider, endpoint: config.domain_finder_endpoint_url, status, parent: row } : null,
        config.email_count_endpoint_url ? { name: 'Hunter Email Count', provider: row.provider, endpoint: config.email_count_endpoint_url, status, parent: row } : null,
        config.bulk_enrichment_endpoint_url ? { name: 'Apollo 批量联系人富化', provider: row.provider, endpoint: config.bulk_enrichment_endpoint_url, status, parent: row } : null,
      ].filter(Boolean)
    })
  } catch (error) { ElMessage.error((error as Error).message) }
}

function httpConfig() {
  const config: Record<string, any> = { adapter: 'http', url: provider.url, method: provider.method, api_key: provider.api_key, api_key_header: provider.api_key_header, response_path: provider.response_path }
  if (provider.headers.trim()) config.headers = parseObject(provider.headers)
  if (provider.type === 'company_search') { config.source_type = provider.source_type; config.query_template = provider.query_template.trim() || '{{brand_keywords}} {{categories}} {{countries}}'; config.country_semantics = provider.country_semantics }
  if (provider.type === 'email_verifier') { config.result_path = provider.result_path; config.score_path = provider.score_path } else { config.items_path = provider.items_path; if (provider.field_map.trim()) config.field_map = parseObject(provider.field_map) }
  return config
}

function builtinConfig() {
  const config: Record<string, any> = { adapter: 'builtin', default_domain_suffix: provider.default_domain_suffix || 'com', limit: provider.limit }
  if (provider.type === 'company_search') { config.brand_keywords = lines(provider.brand_keywords); config.countries = lines(provider.countries); config.categories = lines(provider.categories); if (provider.companies_json.trim()) config.companies = parseJson(provider.companies_json) }
  if (provider.type === 'contact_search') { config.titles = lines(provider.titles); if (provider.contacts_json.trim()) config.contacts = parseJson(provider.contacts_json) }
  if (provider.type === 'email_finder' || provider.type === 'brand_email_search') { config.min_confidence = provider.min_confidence; if (provider.emails_json.trim()) config.emails = parseJson(provider.emails_json) }
  return config
}

function vendorConfig() {
  const config: Record<string, any> = { adapter: provider.adapter, endpoint_url: provider.endpoint_url.trim(), quota_endpoint_url: provider.quota_endpoint_url.trim(), quota_method: provider.quota_method, quota_remaining_path: provider.quota_remaining_path.trim(), api_key: provider.api_key, limit: provider.limit }
  if (provider.quota_api_key_header.trim()) config.quota_api_key_header = provider.quota_api_key_header.trim()
  if (provider.quota_api_key_prefix.trim()) config.quota_api_key_prefix = provider.quota_api_key_prefix.trim()
  if (provider.quota_api_key_query_param.trim()) config.quota_api_key_query_param = provider.quota_api_key_query_param.trim()
  if (catalogAdapters.includes(provider.adapter)) {
    if (provider.api_key_header.trim()) config.api_key_header = provider.api_key_header.trim()
    if (provider.api_key_prefix.trim()) config.api_key_prefix = provider.api_key_prefix.trim()
    if (provider.api_key_query_param.trim()) config.api_key_query_param = provider.api_key_query_param.trim()
    config.request_method = provider.request_method
    config.request_headers = parseObject(provider.request_headers)
    config.request_query = parseObject(provider.request_query)
    config.request_body = parseObject(provider.request_body)
    if (provider.poll_result_url_path.trim()) config.poll_result_url_path = provider.poll_result_url_path.trim()
    config.quota_headers = parseObject(provider.quota_headers)
    config.quota_request_body = parseObject(provider.quota_request_body)
    if (provider.type === 'email_verifier') {
      config.result_path = provider.result_path.trim()
      if (provider.score_path.trim()) config.score_path = provider.score_path.trim()
      config.result_map = parseObject(provider.result_map)
    } else {
      config.response_items_path = provider.response_items_path.trim()
      config.response_field_map = parseObject(provider.response_field_map)
    }
  }
  if (provider.quota_available_path.trim()) config.quota_available_path = provider.quota_available_path.trim()
  if (provider.quota_used_path.trim()) config.quota_used_path = provider.quota_used_path.trim()
  if (provider.quota_reset_at_path.trim()) config.quota_reset_at_path = provider.quota_reset_at_path.trim()
  if (provider.domain_finder_endpoint_url.trim()) config.domain_finder_endpoint_url = provider.domain_finder_endpoint_url.trim()
  if (provider.email_count_endpoint_url.trim()) config.email_count_endpoint_url = provider.email_count_endpoint_url.trim()
  if (provider.bulk_enrichment_endpoint_url.trim()) {
    config.bulk_enrichment_endpoint_url = provider.bulk_enrichment_endpoint_url.trim()
    config.bulk_enrichment_batch_size = provider.bulk_enrichment_batch_size
    config.bulk_enrichment_reveal_personal_emails = provider.bulk_enrichment_reveal_personal_emails
    config.bulk_enrichment_reveal_phone_number = provider.bulk_enrichment_reveal_phone_number
    config.test_bulk_enrichment = provider.test_bulk_enrichment
  }
  if (provider.test_domain_finder_company.trim()) config.test_domain_finder_company = provider.test_domain_finder_company.trim()
  if (provider.adapter === 'apollo' && provider.test_query.trim()) config.test_query = provider.test_query.trim()
  if (provider.type === 'brand_email_search' && provider.test_domain.trim()) config.test_domain = provider.test_domain.trim()
  if (provider.type === 'email_verifier' && provider.test_email.trim()) config.test_email = provider.test_email.trim()
  return config
}

async function toggleProvider(row: any, enabled: boolean) { try { await api.patch(`/provider-configs/${row.id}`, { enabled }); await Promise.all([providerTable.value?.load(), loadCapabilityConfigs()]) } catch (error) { ElMessage.error((error as Error).message) } }
async function testProvider(id: string) { try { const { data } = await api.post(`/provider-configs/${id}/test`); data.ok ? ElMessage.success('Provider 测试成功') : ElMessage.error(data.error_message || 'Provider 测试失败') } catch (error) { ElMessage.error((error as Error).message) } }
async function removeProvider(id: string) { try { await ElMessageBox.confirm('确认删除这个 Provider 配置？', '删除确认', { type: 'warning' }); await api.delete(`/provider-configs/${id}`); await Promise.all([providerTable.value?.load(), loadCapabilityConfigs()]) } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) } }

function parseJson(value: string): any { try { return JSON.parse(value) } catch { throw new Error('JSON 格式不正确') } }
function parseObject(value: string): Record<string, any> { const parsed = parseJson(value); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('必须填写 JSON 对象'); return parsed }
function lines(value: string) { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) }
function linesToText(value: unknown) { return Array.isArray(value) ? value.join('\n') : '' }
function providerTypeLabel(value: string) { return providerTypes.find((item) => item.value === value)?.label || value }
function providerSummary(row: any) { const config = row.config || {}; return `${adapterOptions.find((item) => item.value === config.adapter)?.label || config.adapter || 'HTTP'} · ${config.endpoint_url || config.url || '未填写接口地址'}` }
function moduleLabel(value: string) { return moduleOptions.find((item) => item.value === value)?.label || value }
function fieldTypeLabel(value: string) { return fieldTypeLabels[value] || value }
function normalizePermissions(value: unknown) { const result: Record<string, string[]> = {}; if (!value || Array.isArray(value) || typeof value !== 'object') return result; for (const [resource, actions] of Object.entries(value)) result[resource] = Array.isArray(actions) ? actions.map(String) : [String(actions)]; return result }
function permissionSummary(value: unknown) { const permissions = normalizePermissions(value); const labels = permissionOptions.filter((item) => permissions[item.resource]?.length).map((item) => item.label); return labels.length ? `${labels.slice(0, 4).join('、')}${labels.length > 4 ? `等 ${labels.length} 项` : ''}` : '无额外权限' }
function auditActionLabel(value: string) { const action = value?.split('.').pop() || ''; return ({ create: '创建', update: '修改', delete: '删除', test: '测试', enable: '启用', disable: '停用' } as Record<string, string>)[action] || value }
function auditEntityLabel(value: string) { return ({ role: '角色', user: '用户', provider: '数据源', system_setting: '系统设置', tag: '标签', custom_field: '自定义字段' } as Record<string, string>)[value] || value }
function auditChangeSummary(value: unknown) { if (!value) return '无'; if (typeof value !== 'object') return String(value); const keys = Object.keys(value as object); return keys.length ? `修改了 ${keys.length} 个字段：${keys.slice(0, 3).join('、')}${keys.length > 3 ? '…' : ''}` : '无字段变化' }
function scrollAdapters(direction: number) { adapterScroller.value?.scrollBy({ left: direction * 280, behavior: 'smooth' }) }
function scrollAdaptersByWheel(event: WheelEvent) { scrollAdapters(event.deltaY > 0 ? 1 : -1) }

onMounted(async () => { await reloadCurrentTab() })
</script>

<style scoped>
.vendor-error {
  margin-top: 6px;
  color: #b42318;
  font-size: 12px;
  line-height: 1.4;
}

.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}

.section-help {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
}

.settings-grid {
  display: grid;
  gap: 0 20px;
  margin-top: 18px;
}

.settings-grid--two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.settings-grid--three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.settings-grid :deep(.el-input-number) {
  width: 100%;
}

.grid-span-full {
  grid-column: 1 / -1;
}

.sticky-actions {
  position: sticky;
  bottom: 12px;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 8px;
  padding: 14px 16px;
  border: 1px solid #dbe3ec;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.12);
}

.permission-list {
  width: 100%;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
}

.permission-row {
  display: grid;
  grid-template-columns: minmax(150px, 0.8fr) minmax(260px, 1.2fr);
  align-items: center;
  gap: 16px;
  padding: 13px 16px;
  border-bottom: 1px solid #eef2f6;
}

.permission-row:last-child {
  border-bottom: 0;
}

.permission-row strong,
.permission-row small {
  display: block;
}

.permission-row small {
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
}

.adapter-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.adapter-scroller {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: thin;
  touch-action: pan-x;
}

.adapter-scroller :deep(.el-segmented) {
  width: max-content;
  white-space: nowrap;
}

.provider-advanced {
  width: calc(100% - 130px);
  margin: 12px 0 18px 130px;
}

.provider-advanced :deep(.el-collapse-item__content) {
  padding-top: 16px;
}

@media (max-width: 760px) {
  .page-heading,
  .panel-heading,
  .sticky-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .settings-grid--two,
  .settings-grid--three {
    grid-template-columns: 1fr;
  }

  .permission-row {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .provider-advanced {
    width: 100%;
    margin-left: 0;
  }
}
</style>
