import {test,expect} from "@playwright/test";

test.skip(!process.env.WELCOME_CATALOG_TEST_URL,"Local Welcome server required");

test("outside Telegram does not depend on the external SDK",async({page})=>{
  let sdkRequests=0;
  await page.route("https://telegram.org/js/**",route=>{sdkRequests++;return route.abort();});
  await page.route("**/api/v1/public-config",route=>route.fulfill({json:{interface_bot_username:"GramlyHelloBot"}}));
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/app.html`);
  await expect(page.getByRole("heading",{name:"Управление приветствиями живёт внутри Telegram."})).toBeVisible();
  expect(sdkRequests).toBe(0);
});

test("hung Telegram SDK ends with an actionable error, not endless loading",async({page})=>{
  await page.route("https://telegram.org/js/**",()=>{});
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/app.html#tgWebAppVersion=8.0`,{waitUntil:"domcontentloaded"});
  await expect(page.getByRole("heading",{name:"Telegram не отвечает."})).toBeVisible({timeout:12000});
  await expect(page.getByRole("button",{name:"Повторить",exact:true})).toBeVisible();
});

test("failed Telegram SDK is not mistaken for opening outside Telegram",async({page})=>{
  await page.route("https://telegram.org/js/**",route=>route.abort());
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/app.html#tgWebAppData=test`,{waitUntil:"domcontentloaded"});
  await expect(page.getByRole("heading",{name:"Telegram не отвечает."})).toBeVisible();
});

test("loaded Telegram SDK reaches server-side session validation",async({page})=>{
  await page.route("https://telegram.org/js/**",route=>route.fulfill({contentType:"application/javascript",body:'window.Telegram={WebApp:{initData:"test-only",ready(){},expand(){}}};'}));
  await page.route("**/api/v1/public-config",route=>route.fulfill({json:{interface_bot_username:"GramlyHelloBot"}}));
  let validated=false;
  await page.route("**/api/v1/session/telegram",route=>{validated=true;return route.fulfill({status:401,json:{detail:"Test session rejected"}});});
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/app.html#tgWebAppData=test`,{waitUntil:"domcontentloaded"});
  await expect(page.getByText("Test session rejected",{exact:true})).toBeVisible();
  expect(validated).toBe(true);
});
