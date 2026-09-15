import {test,expect} from "@playwright/test";
test.skip(!process.env.WELCOME_CATALOG_TEST_URL,"Local Welcome server required");

for(const width of [390,1440])test(`admin creates, publishes and archives a template ${width}`,async({page})=>{
  await page.setViewportSize({width,height:932});
  const items=[];
  await page.route("**/api/admin/v1/**",async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==="/api/admin/v1/catalog"){
      if(request.method()==="POST"){
        const payload=request.postDataJSON();expect(payload.expected_revision).toBe(0);
        expect(payload.entry.spec.steps[0].payload.text).toBe("{name}, добро пожаловать!");
        items.push({...payload.entry,id:91,is_active:false});
        return route.fulfill({json:items[0]});
      }
      return route.fulfill({json:{items,total:items.length,page:1}});
    }
    if(url.pathname.endsWith("/catalog/91/publish")){items[0].is_active=true;return route.fulfill({json:items[0]});}
    if(url.pathname.endsWith("/catalog/91")&&request.method()==="DELETE"){items[0].is_active=false;return route.fulfill({json:items[0]});}
    return route.fulfill({json:null});
  });
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/admin.html`);
  await page.getByRole("button",{name:"Шаблоны цепочек Каталог, версии и публикация"}).click();
  await page.getByRole("button",{name:"Добавить шаблон",exact:true}).click();
  await page.getByLabel("Название",{exact:true}).fill("Добро пожаловать в сообщество");
  await page.getByLabel("Описание",{exact:true}).fill("Тестовый шаблон — фикстура, не production-контент.");
  await page.getByLabel("Текст · можно использовать {name}").fill("{name}, добро пожаловать!");
  await page.getByLabel("URL-кнопки").fill("Сайт - https://gramly.tech");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(process.env.CAPTURE_HELLO_FINISH)await page.screenshot({path:`data/ops/admin-catalog-qa/form-${width}.png`,fullPage:true,animations:"disabled"});
  await page.getByRole("button",{name:"Сохранить скрытую версию"}).click();
  await expect(page.getByRole("status")).toContainText("сохранена и скрыта");
  page.on("dialog",dialog=>dialog.accept());
  await page.getByRole("button",{name:"Опубликовать",exact:true}).click();
  await expect(page.getByRole("status")).toHaveText("Шаблон опубликован.");
  if(process.env.CAPTURE_HELLO_FINISH)await page.screenshot({path:`data/ops/admin-catalog-qa/list-${width}.png`,fullPage:true,animations:"disabled"});
  await page.getByRole("button",{name:"Убрать из каталога"}).click();
  await expect(page.getByRole("status")).toContainText("Клиентские копии сохранены");
  expect(items).toHaveLength(1);
  expect(items[0].is_active).toBe(false);
});

test("admin catalog ignores a late response from the previous search",async({page})=>{
  let releaseOld;
  const oldResponse=new Promise(resolve=>{releaseOld=resolve});
  await page.route("**/api/admin/v1/**",async route=>{
    const url=new URL(route.request().url());
    if(!url.pathname.endsWith("/catalog"))return route.fulfill({json:null});
    const q=url.searchParams.get("q");
    if(q==="old")await oldResponse;
    const items=q?[{id:q==="old"?1:2,slug:q,revision:1,title:q,kind:"welcome",description:"Test",author:"Gramly",tags:[],screenshots:[],is_active:false,spec:{first_delay_seconds:0,steps:[]}}]:[];
    return route.fulfill({json:{items,total:items.length,page:1}});
  });
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/admin.html`);
  await page.getByRole("button",{name:"Шаблоны цепочек Каталог, версии и публикация"}).click();
  const oldRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get("q")==="old");
  await page.getByLabel("Поиск",{exact:true}).fill("old");await oldRequest;
  await page.getByLabel("Поиск",{exact:true}).fill("new");
  await expect(page.getByRole("heading",{name:"new",exact:true})).toBeVisible();
  const oldFinished=page.waitForResponse(response=>new URL(response.url()).searchParams.get("q")==="old");
  releaseOld();await oldFinished;
  await expect(page.getByRole("heading",{name:"new",exact:true})).toBeVisible();
  await expect(page.getByRole("heading",{name:"old",exact:true})).toHaveCount(0);
});
