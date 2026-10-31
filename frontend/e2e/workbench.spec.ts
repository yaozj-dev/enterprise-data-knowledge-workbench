import { expect, test, type BrowserContext } from "@playwright/test";

const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const hasAdminCredentials = Boolean(adminEmail && adminPassword);

test.describe("企业数据分析与制度知识问答工作台浏览器流程", () => {
  test.skip(!hasAdminCredentials, "需要设置 E2E_ADMIN_EMAIL 和 E2E_ADMIN_PASSWORD 才能运行真实环境端到端测试。");

  test("新用户可自行注册并进入隔离工作区", async ({ page, browser }) => {
    if (!adminEmail || !adminPassword) throw new Error("缺少 E2E 管理员账号环境变量。");

    const timestamp = Date.now();
    const email = `e2e-signup-${timestamp}@workbench.local`;
    const password = `E2eSignup-${timestamp}-safe`;
    const memberContext = await browser.newContext();
    const memberPage = await memberContext.newPage();
    let userId: number | undefined;

    try {
      await memberPage.goto("/");
      await memberPage.getByRole("button", { name: "注册账号" }).click();
      await expect(memberPage.getByRole("heading", { name: "注册工作台账号" })).toBeVisible();
      if (process.env.UPDATE_DEMO_SCREENSHOTS === "true") {
        await memberPage.screenshot({ path: "../docs/assets/workbench-register.png", fullPage: false });
      }
      await memberPage.getByLabel("姓名").fill("E2E 注册用户");
      await memberPage.getByLabel("邮箱地址").fill(email);
      await memberPage.getByLabel("密码", { exact: true }).fill(password);
      await memberPage.getByLabel("确认密码").fill(password);
      await memberPage.getByRole("button", { name: "创建账号" }).click();
      await expect(memberPage.getByRole("button", { name: /对话工作台/ })).toBeVisible();
      await expect(memberPage.getByRole("button", { name: /管理员控制台/ })).toHaveCount(0);

      userId = await memberPage.evaluate(async () => {
        const token = window.localStorage.getItem("workbench_token");
        const response = await fetch("/api/workbench/auth/me", {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        const user = await response.json() as { id: number; role: string };
        if (!response.ok || user.role !== "user") throw new Error("注册账号未以普通用户身份登录");
        return user.id;
      });

      await page.goto("/");
      await page.getByLabel("邮箱地址").fill(adminEmail);
      await page.getByLabel("密码").fill(adminPassword);
      await page.getByRole("button", { name: "登录工作台" }).click();
      await expect(page.getByRole("button", { name: /管理员控制台/ })).toBeVisible();
    } finally {
      await memberContext.close();
      if (userId) {
        const status = await page.evaluate(async (id) => {
          const token = window.localStorage.getItem("workbench_token");
          const response = await fetch(`/api/workbench/admin/users/${encodeURIComponent(String(id))}`, {
            method: "DELETE",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          return response.status;
        }, userId);
        expect(status).toBe(200);
      }
    }
  });

  test("管理员可以追踪上传操作，普通用户不能读取管理员数据", async ({ page, browser }) => {
    if (!adminEmail || !adminPassword) throw new Error("缺少 E2E 管理员账号环境变量。");

    const timestamp = Date.now();
    const filename = `e2e-policy-${timestamp}.md`;
    const content = `# 浏览器端到端测试文档\n\n该文件由 Playwright 创建，并会在测试结束时删除。\n\n编号：${timestamp}`;
    const memberEmail = `e2e-member-${timestamp}@workbench.local`;
    const memberPassword = `E2eMember-${timestamp}-safe`;
    let documentId: string | undefined;
    let memberId: number | undefined;
    let memberContext: BrowserContext | undefined;

    await test.step("登录工作台", async () => {
      await page.goto("/");
      await page.getByLabel("邮箱地址").fill(adminEmail);
      await page.getByLabel("密码").fill(adminPassword);
      await page.getByRole("button", { name: "登录工作台" }).click();
      await expect(page.getByRole("button", { name: /对话工作台/ })).toBeVisible();
    });

    try {
      await test.step("通过界面上传并索引临时文档", async () => {
        await page.getByRole("button", { name: /企业知识库/ }).click();
        await expect(page.getByRole("heading", { name: "企业知识库" })).toBeVisible();

        const uploadResponse = page.waitForResponse(
          (response) => response.request().method() === "POST" && response.url().endsWith("/api/workbench/documents"),
        );
        await page.locator('input[type="file"]').setInputFiles({
          name: filename,
          mimeType: "text/markdown",
          buffer: Buffer.from(content, "utf8"),
        });

        const response = await uploadResponse;
        expect(response.status()).toBe(200);
        documentId = (await response.json() as { id: string }).id;

        await expect(page.getByText(filename, { exact: true })).toBeVisible();
        await expect(page.getByText("可检索", { exact: true }).first()).toBeVisible();
      });

      await test.step("管理员界面展示上传审计", async () => {
        await page.getByRole("button", { name: /管理员控制台/ }).click();
        await expect(page.getByRole("heading", { name: "管理员控制台" })).toBeVisible();
        await expect(page.locator("code").filter({ hasText: "upload_document" }).first()).toBeVisible();
        await expect(page.getByText(filename, { exact: true })).toBeVisible();
      });

      await test.step("创建普通用户并验证权限隔离", async () => {
        const createResponse = page.waitForResponse(
          (response) => response.request().method() === "POST" && response.url().endsWith("/api/workbench/admin/users"),
        );
        await page.getByLabel("姓名").fill("E2E 普通用户");
        await page.getByLabel("邮箱").fill(memberEmail);
        await page.getByLabel("初始密码").fill(memberPassword);
        await page.getByLabel("角色").selectOption("user");
        await page.getByRole("button", { name: "创建用户" }).click();

        const response = await createResponse;
        expect(response.status()).toBe(201);
        memberId = (await response.json() as { id: number }).id;
        await expect(page.locator("table").first().getByText(memberEmail, { exact: true })).toBeVisible();

        memberContext = await browser.newContext();
        const memberPage = await memberContext.newPage();
        await memberPage.goto(process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173");
        await memberPage.getByLabel("邮箱地址").fill(memberEmail);
        await memberPage.getByLabel("密码").fill(memberPassword);
        await memberPage.getByRole("button", { name: "登录工作台" }).click();
        await expect(memberPage.getByRole("button", { name: /对话工作台/ })).toBeVisible();
        await expect(memberPage.getByRole("button", { name: /管理员控制台/ })).toHaveCount(0);

        await memberPage.getByRole("button", { name: /企业知识库/ }).click();
        await expect(memberPage.getByRole("heading", { name: "企业知识库" })).toBeVisible();
        await expect(memberPage.getByText(filename, { exact: true })).toHaveCount(0);

        const adminStatus = await memberPage.evaluate(async () => {
          const token = window.localStorage.getItem("workbench_token");
          const response = await fetch("/api/workbench/admin/users", {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          return response.status;
        });
        expect(adminStatus).toBe(403);
      });

      await test.step("管理员通过界面删除临时普通用户", async () => {
        if (!memberId) throw new Error("临时普通用户未创建。");
        await memberContext?.close();
        memberContext = undefined;

        const deleteResponse = page.waitForResponse(
          (response) => response.request().method() === "DELETE" && response.url().endsWith(`/api/workbench/admin/users/${memberId}`),
        );
        page.once("dialog", (dialog) => void dialog.accept());
        await page.getByRole("button", { name: `删除 ${memberEmail}` }).click();
        const response = await deleteResponse;
        expect(response.status()).toBe(200);
        await expect(page.locator("table").first().getByText(memberEmail, { exact: true })).toHaveCount(0);
        memberId = undefined;
      });
    } finally {
      await memberContext?.close();
      if (memberId) {
        await page.evaluate(async (id) => {
          const token = window.localStorage.getItem("workbench_token");
          await fetch(`/api/workbench/admin/users/${encodeURIComponent(String(id))}`, {
            method: "DELETE",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
        }, memberId);
      }
      if (documentId) {
        await test.step("清理临时文档", async () => {
          const status = await page.evaluate(async (id) => {
            const token = window.localStorage.getItem("workbench_token");
            const response = await fetch(`/api/workbench/documents/${encodeURIComponent(id)}`, {
              method: "DELETE",
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            return response.status;
          }, documentId);
          expect(status).toBe(200);
        });
      }
    }
  });

  test("管理员可从一次执行 Trace 审核用户反馈", async ({ page }) => {
    if (!adminEmail || !adminPassword) throw new Error("缺少 E2E 管理员账号环境变量。");

    await page.goto("/");
    await page.getByLabel("邮箱地址").fill(adminEmail);
    await page.getByLabel("密码").fill(adminPassword);
    await page.getByRole("button", { name: "登录工作台" }).click();
    await expect(page.getByRole("button", { name: /对话工作台/ })).toBeVisible();

    await page.getByRole("button", { name: "新建会话", exact: true }).click();
    await expect(page.getByText("本会话完成 0 次请求")).toBeVisible();
    const assistantMessages = page.getByTestId("assistant-message");
    const assistantMessageCount = await assistantMessages.count();

    await page.getByPlaceholder("输入业务问题，按 Enter 发送，Shift + Enter 换行").fill("请告诉我当前日期（质量运营演示）");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(assistantMessages).toHaveCount(assistantMessageCount + 1);
    const currentAssistantMessage = assistantMessages.nth(assistantMessageCount);
    await expect(currentAssistantMessage.getByText(/今天是\d{4}年/)).toBeVisible();
    await expect(currentAssistantMessage).toHaveAttribute("data-trace-id", /.+/);

    await currentAssistantMessage.getByRole("button", { name: "需要改进" }).click();
    await currentAssistantMessage.getByLabel("问题类型").selectOption("wrong_route");
    await currentAssistantMessage.getByLabel("补充说明").fill("示例反馈：请审核这次执行，并纳入后续回归测试。");
    const feedbackResponse = page.waitForResponse(
      (response) => response.request().method() === "POST" && /\/api\/workbench\/agent-runs\/[^/]+\/feedback$/.test(response.url()),
    );
    await currentAssistantMessage.getByRole("button", { name: "提交" }).click();
    const feedbackSubmission = await feedbackResponse;
    expect(feedbackSubmission.status()).toBe(200);
    const feedback = await feedbackSubmission.json() as { run_id: string };
    await expect(currentAssistantMessage.getByText("已进入质量审核队列")).toBeVisible();

    await page.getByRole("button", { name: /质量运营/ }).click();
    await expect(page.getByRole("heading", { name: "Agent 质量运营" })).toBeVisible();
    const feedbackRow = page.locator(`[data-testid="quality-feedback-row"][data-run-id="${feedback.run_id}"]`);
    await expect(feedbackRow).toBeVisible();
    await feedbackRow.getByLabel("期望路由").selectOption("general");
    await feedbackRow.getByLabel("审核说明").fill("已确认该通用日期请求可作为审核演示样本。");
    const reviewResponse = page.waitForResponse(
      (response) => response.request().method() === "POST" && /\/api\/workbench\/admin\/quality\/feedback\/\d+\/review$/.test(response.url()),
    );
    await feedbackRow.getByRole("button", { name: "接受" }).click();
    expect((await reviewResponse).status()).toBe(200);
    await expect(feedbackRow.getByText("已接受")).toBeVisible();
    if (process.env.UPDATE_DEMO_SCREENSHOTS === "true") {
      await page.screenshot({ path: "../docs/assets/workbench-quality-operations.png", fullPage: false });
    }
  });
});
