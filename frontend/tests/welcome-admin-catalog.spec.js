import {test,expect} from "@playwright/test";
test.skip(!process.env.WELCOME_CATALOG_TEST_URL,"Local Welcome server required");

for(const width of [390,1440])test(`admin creates, publishes and archives a template ${width}`,async({page})=>{
  await page.setViewportSize({width,height:932});
  const items=[];
  let uploads=0;
  await page.route("**/api/admin/v1/**",async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==="/api/admin/v1/catalog-media"){uploads++;return route.fulfill({json:{media_type:"photo",storage_key:`catalog/uploads/${(uploads===1?"a":"b").repeat(64)}.png`,original_name:`media-${uploads}.png`,mime_type:"image/png",size:70,payload:{}}});}
    if(url.pathname.startsWith("/api/admin/v1/catalog-media/"))return route.fulfill({contentType:"image/png",body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1cAAAAASUVORK5CYII=","base64")});
    if(url.pathname==="/api/admin/v1/catalog"){
      if(request.method()==="POST"){
        const payload=request.postDataJSON();expect(payload.expected_revision).toBe(0);
        expect(payload.entry.spec.steps[0].payload.text).toBe("{name}, добро пожаловать!");
        expect(payload.entry.spec.steps[0].attachments).toHaveLength(1);
        expect(payload.entry.spec.steps[0].attachments[0].original_name).toBe("media-2.png");
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
  await page.getByLabel("Добавить файл к сообщению 1").setInputFiles({name:"test.png",mimeType:"image/png",buffer:Buffer.from("test")});
  await expect(page.getByRole("img",{name:"Вложение 1"})).toBeVisible();
  await page.getByLabel("Добавить файл к сообщению 1").setInputFiles({name:"second.png",mimeType:"image/png",buffer:Buffer.from("second")});
  await expect(page.getByRole("img",{name:"Вложение 2"})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(process.env.CAPTURE_HELLO_FINISH)await page.screenshot({path:`data/ops/admin-catalog-qa/form-${width}.png`,fullPage:true,animations:"disabled"});
  await page.getByRole("button",{name:"Выше",exact:true}).nth(1).click();
  await expect(page.locator(".catalog-media").first()).toContainText("media-2.png");
  await page.getByRole("button",{name:"Убрать вложение",exact:true}).nth(1).click();
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
